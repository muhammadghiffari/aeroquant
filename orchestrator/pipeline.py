"""Single-cycle sequential pipeline runner.

Order of operations per cycle (fully autonomous):
1. market clock pre-check (skippable via `force`)
2. account snapshot + ledger load
3. POSITION MANAGEMENT FIRST (autonomous exits / anti-assignment)
4. kill-switch evaluation
5. for each symbol: quant -> sub-agents -> managers -> chief -> risk -> execute
6. persist cycle_report.json + ledger
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import config
from alerts import emit_stage, notify_cycle_events, send_alert, send_daily_summary, telegram_health_check
from agents.context_manager import ContextManager
from agents.news_earnings_agent import NewsEarningsAgent
from agents.risk_manager_agent import decide as risk_decide
from agents.strategy_decision_agent import StrategyDecisionAgent, validate_candidate_choice
from agents.technical_manager import TechnicalManager
from agents.underlying_trend_agent import UnderlyingTrendAgent
from agents.volatility_agent import VolatilityAgent
from data_engine import alpaca_client
from data_engine.mcp_alpaca import fetch_cycle_context
from data_engine.news_data import get_recent_news
from data_engine.option_data import fetch_chain
from execution import executor, ledger, operational_store, position_manager
from execution import shadow_store
from orchestrator.langgraph_cycle import run_symbol_graph
from agents.risk_manager_agent import validate_quant_entry
from runtime_safety import account_identity_error, configuration_errors

log = logging.getLogger(__name__)


def _load_lessons() -> list[str]:
    """Cross-cycle lessons the system wrote for itself (state/lessons.json)."""
    path = config.STATE_DIR / "lessons.json"
    try:
        with open(path, encoding="utf-8") as f:
            lessons = json.load(f)
        return [str(x) for x in lessons][-config.LESSONS_MAX:]
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def _market_open() -> tuple[bool, str]:
    clock = alpaca_client.safe("get_clock", alpaca_client.trading_client().get_clock)
    return bool(clock.is_open), str(clock.next_open)


def run_cycle(symbols: list[str], force: bool = False, dry_run: bool = False) -> dict:
    t_start = time.time()
    report: dict = {
        "cycle_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "forced": force,
        "dry_run": dry_run,
        "symbols": symbols,
    }

    # 0. pre-check -----------------------------------------------------------
    if not force:
        is_open, next_open = _market_open()
        if not is_open:
            report["skipped"] = f"market closed (opens {next_open})"
            if datetime.now(config.MARKET_TIMEZONE).hour >= 16:
                report["daily_summary"] = send_daily_summary(ledger.load())
            report["notifications"] = notify_cycle_events([], [], ledger.load(), cycle=report)
            return report

    # 1. account + ledger ----------------------------------------------------
    account = alpaca_client.safe("get_account", alpaca_client.trading_client().get_account)
    equity = float(account.equity or 0)
    buying_power = float(account.buying_power or 0)
    account_id = str(getattr(account, "id", "unknown"))
    report["account"] = {"account_id": account_id, "equity": equity, "buying_power": buying_power}
    if not dry_run:
        runtime_errors = configuration_errors(
            require_llm=True, require_autonomy=True, require_telegram=True
        )
        identity_error = account_identity_error(account)
        if identity_error:
            runtime_errors.append(identity_error)
        if not runtime_errors:
            telegram_ok, telegram_reason = telegram_health_check()
            if not telegram_ok:
                runtime_errors.append(f"TELEGRAM_HEALTHCHECK_FAILED: {telegram_reason}")
        if runtime_errors:
            report["blocked"] = "runtime_preflight"
            report["runtime_errors"] = runtime_errors
            report["duration_s"] = round(time.time() - t_start, 1)
            send_alert("runtime_preflight", "; ".join(runtime_errors))
            report["notifications"] = notify_cycle_events([], [], ledger.load(), cycle=report)
            fname = config.REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_cycle_report.json"
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1, default=str)
            return report
    if not dry_run:
        operational_store.record_cycle(report["cycle_id"], account_id)
    # 2. manage existing positions first -------------------------------------
    if dry_run:
        data = ledger.load()
        exits = []
    else:
        with ledger.ledger_transaction():
            data = ledger.load()
            exits = position_manager.manage_positions(data)
            # Persist recovery and broker reconciliation before model work can fail.
            ledger.save(data)
    report["position_exits"] = exits
    if dry_run:
        report["position_management_skipped"] = True
    if exits:
        log.info("%d position(s) exited this cycle", len(exits))

    # 3. kill switch ----------------------------------------------------------
    killed, reason = ledger.kill_switch_active(data, equity)
    if killed:
        report["new_entries_blocked"] = f"kill switch: {reason}"
        log.warning("kill switch active: %s", reason)
        send_alert("kill_switch", reason)

    # 4. per-symbol analysis pipeline -----------------------------------------
    mcp_ctx = fetch_cycle_context(symbols) if not report.get("skipped") and not killed else {}
    report["mcp_context"] = {
        "mcp_used": bool(mcp_ctx),
        "clock": mcp_ctx.get("clock"),
        "news_symbols": sorted((mcp_ctx.get("news") or {}).keys()),
    }

    results = []
    for symbol in symbols:
        stage_callback = _make_stage_callback(report, symbol) if not dry_run else None
        _stage(stage_callback, "CYCLE_STARTED", {"status": "started"})
        if killed:
            _skip_stages(
                stage_callback,
                "kill_switch",
                ("QUANT_COMPLETED", "STRATEGY_DECIDED", "RISK_DECIDED", "ORDER_SUBMITTED"),
            )
            result = {"symbol": symbol, "action": "BLOCKED_KILL_SWITCH"}
            results.append(result)
            _stage(stage_callback, "CYCLE_COMPLETED", {"status": "blocked", "action": result["action"]})
            continue
        try:
            res = run_symbol_graph(
                symbol.upper(),
                lambda item: _process_symbol(
                    item, account, data, equity, mcp_ctx, dry_run=dry_run,
                    cycle_id=report["cycle_id"], account_id=account_id,
                    stage_callback=stage_callback,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("symbol %s failed", symbol)
            res = {"symbol": symbol, "error": str(exc)}
        results.append(res)
        _stage(
            stage_callback,
            "CYCLE_COMPLETED",
            {
                "status": "failed" if res.get("error") else "completed",
                "action": res.get("action"),
                "error": res.get("error"),
            },
        )

    report["results"] = results
    from llm import provider_available

    report["llm_available"] = provider_available()
    report["llm_usage"] = _aggregate_usage(results)
    report["duration_s"] = round(time.time() - t_start, 1)
    if not dry_run:
        with ledger.ledger_transaction():
            data = ledger.load()
        report["notifications"] = notify_cycle_events(results, exits, data, cycle=report)

    # 5. persist ---------------------------------------------------------------
    fname = config.REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_cycle_report.json"
    if not dry_run:
        try:
            from evaluation import run_after_cycle

            report["evaluation"] = run_after_cycle(data, results)
        except Exception:  # noqa: BLE001
            log.exception("evaluation crashed (cycle continues)")
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    log.info("cycle %s done in %.1fs -> %s", report["cycle_id"], report["duration_s"], fname.name)
    return report


def _aggregate_usage(results: list[dict]) -> dict:
    tot = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "by_agent": {}}
    for r in results:
        for rep in (r.get("reports") or {}).values():
            u = rep.get("_usage") if isinstance(rep, dict) else None
            if not u:
                continue
            tot["input_tokens"] += u.get("input_tokens", 0)
            tot["output_tokens"] += u.get("output_tokens", 0)
            tot["calls"] += u.get("calls", 0)
            name = rep.get("agent", "?")
            agg = tot["by_agent"].setdefault(name, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
            for k in ("input_tokens", "output_tokens", "calls"):
                agg[k] += u.get(k, 0)
    return tot


def _news_gate_passes(report: dict, headlines: list) -> bool:
    """Require a valid, sufficiently confident analysis of supplied headlines."""
    if not isinstance(report, dict) or not headlines:
        return False
    if str(report.get("event_risk", "")).upper() not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return False
    if str(report.get("sentiment", "")).upper() not in {"POSITIVE", "NEGATIVE", "NEUTRAL"}:
        return False
    try:
        confidence = float(report.get("confidence"))
    except (TypeError, ValueError):
        return False
    return config.NEWS_MIN_CONFIDENCE <= confidence <= 1.0


_ACTIVE_POSITION_STATES = {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}


def _stage(stage_callback, stage: str, details: dict | None = None) -> None:
    if stage_callback is not None:
        stage_callback(stage, details or {})


def _skip_stages(stage_callback, reason: str, stages: tuple[str, ...]) -> None:
    for stage_name in stages:
        _stage(stage_callback, stage_name, {"status": "skipped", "reason": reason})


def _make_stage_callback(report: dict, symbol: str):
    sequence = 0

    def callback(stage_name: str, details: dict | None = None) -> None:
        nonlocal sequence
        result = emit_stage(report["cycle_id"], symbol, stage_name, sequence, details)
        report.setdefault("stage_events", []).append(result)
        sequence += 1

    return callback


def _process_symbol(
    symbol: str,
    account,
    data: dict,
    equity: float,
    mcp_ctx: dict | None = None,
    dry_run: bool = False,
    cycle_id: str = "",
    account_id: str = "unknown",
    stage_callback=None,
) -> dict:
    res: dict = {"symbol": symbol}
    mcp_ctx = mcp_ctx or {}
    if any(
        str(position.get("underlying", "")).upper() == symbol
        and position.get("status") in _ACTIVE_POSITION_STATES
        for position in data["positions"]
    ):
        _stage(stage_callback, "QUANT_COMPLETED", {"status": "skipped", "reason": "active_position"})
        _skip_stages(
            stage_callback,
            "active_position",
            ("STRATEGY_DECIDED", "RISK_DECIDED", "ORDER_SUBMITTED"),
        )
        return {"symbol": symbol, "action": "SKIPPED_ACTIVE_POSITION"}

    qr_start = time.time()
    from quant_engine.engine import build_quant_report

    quant = build_quant_report(symbol)
    if quant is None:
        _stage(stage_callback, "QUANT_COMPLETED", {"status": "failed", "reason": "no_usable_data"})
        _skip_stages(
            stage_callback,
            "quant_failed",
            ("STRATEGY_DECIDED", "RISK_DECIDED", "ORDER_SUBMITTED"),
        )
        return {"symbol": symbol, "error": "quant engine produced no usable data"}
    quant_gate = quant.get("momentum") or {}
    res["quant_gate"] = quant_gate
    _stage(stage_callback, "QUANT_COMPLETED", {
        "status": "completed",
        "direction": quant_gate.get("direction") or quant_gate.get("directional_bias"),
        "entry_actionable": quant_gate.get("entry_actionable", False),
        "mode": quant_gate.get("entry_mode", "NONE"),
    })
    if not dry_run and config.SHADOW_ANALYSIS_ENABLED:
        shadow_results = [shadow_store.process_snapshot(
            symbol,
            quant_gate.get("shadow_context") or {},
            quant_gate.get("shadow_candidates") or [],
        )]
        daily_context = quant_gate.get("daily_shadow_context")
        if daily_context:
            shadow_results.append(shadow_store.process_snapshot(symbol, daily_context, []))
        res["shadow"] = {
            "resolved": sum(item.get("resolved", 0) for item in shadow_results),
            "recorded": sum(item.get("recorded", 0) for item in shadow_results),
        }
    shadow_analysis_candidates = [
        {**item, "shadow_only": True}
        for item in (quant_gate.get("shadow_candidates") or [])
        if (item.get("profitability") or {}).get("valid")
    ]
    direction = str(quant_gate.get("direction", "")).upper()
    if direction not in {"BULLISH", "BEARISH"}:
        direction = str(quant_gate.get("directional_bias", "WAIT")).upper()
    analysis_only = (
        config.SHADOW_ANALYSIS_ENABLED
        and bool(shadow_analysis_candidates)
        and direction in {"BULLISH", "BEARISH"}
        and not quant_gate.get("entry_actionable")
    )
    if not quant_gate.get("entry_actionable"):
        confidence = quant_gate.get("confidence") or {}
        if not analysis_only and confidence.get("state") in {"WAIT_SEE", "AMBER"}:
            res["action"] = "WAIT_SEE"
            res["confidence"] = confidence
            res["rejection_reason"] = ";".join(confidence.get("reasons") or [])
        elif not analysis_only and confidence.get("state") == "WAIT_DATA":
            res["action"] = "WAIT_DATA"
            res["confidence"] = confidence
            res["rejection_reason"] = ";".join(confidence.get("reasons") or [])
        elif not analysis_only:
            res["action"] = "WAIT_QUANT_GATE"
        if not analysis_only:
            _skip_stages(
                stage_callback,
                res["action"],
                ("STRATEGY_DECIDED", "RISK_DECIDED", "ORDER_SUBMITTED"),
            )
            return res
        quant_gate["analysis_only"] = True
    spot = quant["underlying_price"]
    res["quant_summary"] = {
        "price": spot,
        "iv_rank_proxy": quant["volatility"].get("iv_rank_proxy_hv_based"),
        "hv_iv_spread": quant["volatility"].get("hv_iv_spread"),
        "z_score_20d": quant["trend"]["z_score_20d"],
        "expected_move_pct": quant["expected_move"].get("expected_move_pct"),
    }

    trend = UnderlyingTrendAgent().run(quant)
    vol = VolatilityAgent().run(quant)

    # news: MCP-first (hackathon requirement), alpaca-py fallback
    headlines = (mcp_ctx.get("news") or {}).get(symbol) or []
    news_source = "mcp_server" if headlines else "alpaca_py_sdk"
    if not headlines:
        headlines = [h["headline"] for h in get_recent_news(symbol)]
    news_payload = {
        "symbol": symbol,
        "headlines": headlines,
        "earnings": quant["earnings"],
    }
    news = NewsEarningsAgent().run(news_payload)
    news["source"] = news_source
    news["headlines"] = headlines[:8]
    if not _news_gate_passes(news, headlines) or news.get("event_risk") == "CRITICAL":
        res["action"] = "WAIT_NEWS_GATE"
        res["rejection_reason"] = (
            "critical_news" if str(news.get("event_risk", "")).upper() == "CRITICAL"
            else "news_analysis_invalid"
        )
        res["reports"] = {"quant_gate": quant_gate, "news": news}
        _skip_stages(
            stage_callback,
            res["action"],
            ("STRATEGY_DECIDED", "RISK_DECIDED", "ORDER_SUBMITTED"),
        )
        return res

    technical = TechnicalManager().run({"quant": quant, "trend": trend, "volatility": vol})
    context = ContextManager().run({"news_report": news, "earnings": quant["earnings"]})

    chain = [] if analysis_only else fetch_chain(
        symbol,
        min_dte=config.MOMENTUM_MIN_DTE,
        max_dte=config.MOMENTUM_MAX_DTE,
        max_spread_pct=config.MOMENTUM_MAX_SPREAD_PCT,
    )
    candidates = shadow_analysis_candidates if analysis_only else quant_gate.get("candidates", [])

    chief_payload = {
        "symbol": symbol,
        "technical_report": technical,
        "trend_report": trend,
        "volatility_report": vol,
        "context_report": context,
        "spot_price": spot,
        "quant_gate": quant_gate,
        "candidate_whitelist": candidates,
        "buying_power": float(account.buying_power or 0),
        "lessons_from_past": _load_lessons(),
    }

    proposal = StrategyDecisionAgent().run(chief_payload)
    proposal["_earnings_days"] = quant["earnings"]["earnings_proximity_days"]
    candidate_valid = validate_candidate_choice(proposal, candidates)
    _stage(stage_callback, "STRATEGY_DECIDED", {
        "status": "completed",
        "action": proposal.get("strategy_type"),
        "candidate_id": proposal.get("candidate_id"),
    })
    if analysis_only:
        res["action"] = "SHADOW_ONLY"
        res["analysis_only"] = True
        res["candidate_valid"] = candidate_valid
        res["execution"] = None
        res["reports"] = {
            "trend": trend,
            "volatility": vol,
            "news": news,
            "technical": technical,
            "context": context,
            "quant_gate": quant_gate,
            "candidate_whitelist": candidates,
            "proposal": proposal,
        }
        res["llm_seconds"] = round(time.time() - qr_start, 1)
        _skip_stages(stage_callback, "shadow_only", ("RISK_DECIDED", "ORDER_SUBMITTED"))
        return res
    quant_valid, quant_reason = validate_quant_entry(proposal, quant_gate)
    if proposal.get("strategy_type") != "WAIT" and (not candidate_valid or not quant_valid):
        res["action"] = "REJECTED_QUANT_GATE"
        res["rejection_reason"] = quant_reason or "candidate_choice_invalid"
        res["reports"] = {"proposal": proposal, "quant_gate": quant_gate}
        _skip_stages(
            stage_callback,
            res["action"],
            ("RISK_DECIDED", "ORDER_SUBMITTED"),
        )
        return res
    exposure_pct = position_manager.exposure_pct(data)
    symbol_exposure_pct = position_manager.symbol_exposure_pct(data, symbol, equity)
    n_open = position_manager.open_positions_count(data)
    decision = risk_decide(
        proposal, chain, account,
        exposure_used_pct=exposure_pct,
        open_positions_count=n_open,
        technical_report=technical,
        context_report=context,
        volatility_report=vol,
        symbol_exposure_used_pct=symbol_exposure_pct,
    )

    res["reports"] = {
        "trend": trend,
        "volatility": vol,
        "news": news,
        "technical": technical,
        "context": context,
        "quant_gate": quant_gate,
        "candidate_whitelist": candidates,
        "proposal": proposal,
        "risk_decision": decision,
    }
    res["llm_seconds"] = round(time.time() - qr_start, 1)

    stype = proposal.get("strategy_type")
    if stype == "WAIT":
        res["action"] = "WAIT"
        _stage(stage_callback, "RISK_DECIDED", {"status": "completed", "decision": decision.get("decision")})
        _stage(stage_callback, "ORDER_SUBMITTED", {"status": "skipped", "action": res["action"]})
        return res

    if decision["decision"] != "APPROVED":
        res["action"] = "REJECTED"
        _stage(stage_callback, "RISK_DECIDED", {"status": "completed", "decision": decision.get("decision")})
        _stage(stage_callback, "ORDER_SUBMITTED", {"status": "skipped", "action": res["action"]})
        if not dry_run:
            with ledger.ledger_transaction():
                current_data = ledger.load()
                ledger.bump_rejected_streak(current_data)
                ledger.save(current_data)
        return res

    if dry_run:
        res["action"] = "DRY_RUN"
        res["dry_run"] = True
        _stage(stage_callback, "RISK_DECIDED", {"status": "completed", "decision": decision.get("decision")})
        _stage(stage_callback, "ORDER_SUBMITTED", {"status": "skipped", "action": res["action"]})
        return res

    with ledger.ledger_transaction():
        latest_data = ledger.load()
        if any(
            str(position.get("underlying", "")).upper() == symbol
            and position.get("status") in _ACTIVE_POSITION_STATES
            for position in latest_data["positions"]
        ):
            res["action"] = "BLOCKED_ACTIVE_POSITION"
            _stage(stage_callback, "RISK_DECIDED", {"status": "blocked", "decision": "REJECTED", "reason": "active_position"})
            _stage(stage_callback, "ORDER_SUBMITTED", {"status": "skipped", "action": res["action"]})
            return res

        existing_intent = operational_store.find_unresolved_intent(account_id, symbol)
        if existing_intent:
            res["action"] = "BLOCKED_UNRESOLVED_INTENT"
            res["intent_id"] = existing_intent["intent_id"]
            _stage(stage_callback, "RISK_DECIDED", {"status": "blocked", "decision": "REJECTED", "reason": "unresolved_intent"})
            _stage(stage_callback, "ORDER_SUBMITTED", {"status": "skipped", "action": res["action"]})
            return res

        final_decision = risk_decide(
            proposal, chain, account,
            exposure_used_pct=position_manager.exposure_pct(latest_data),
            open_positions_count=position_manager.open_positions_count(latest_data),
            technical_report=technical,
            context_report=context,
            volatility_report=vol,
            symbol_exposure_used_pct=position_manager.symbol_exposure_pct(
                latest_data, symbol, equity
            ),
            use_llm_sanity=False,
        )
        res["reports"]["risk_decision"] = final_decision
        _stage(stage_callback, "RISK_DECIDED", {
            "status": "completed",
            "decision": final_decision.get("decision"),
        })
        if final_decision["decision"] != "APPROVED":
            res["action"] = "REJECTED"
            res["rejection_reason"] = "risk_rejected_after_state_refresh"
            _stage(stage_callback, "ORDER_SUBMITTED", {"status": "skipped", "action": res["action"]})
            ledger.bump_rejected_streak(latest_data)
            ledger.save(latest_data)
            return res

        decision = final_decision
        ledger.reset_rejected_streak(latest_data)
        intent_id = str(uuid.uuid4())
        client_order_id = executor.build_client_order_id(
            proposal, decision["recomputed"]["resolved_legs"]
        )
        operational_store.create_order_intent(
            intent_id, cycle_id, account_id, client_order_id, proposal
        )
        exec_result = executor.submit_strategy(proposal, decision, client_order_id=client_order_id)
        operational_store.record_order_ack(intent_id, exec_result["order_id"], exec_result["status"])
        pos_id = str(uuid.uuid4())[:8]
        broker_status = str(exec_result.get("status", "")).split(".")[-1].lower()
        position_status = "OPEN" if broker_status == "filled" else "PENDING_ENTRY"
        position = {
            "id": pos_id,
            "underlying": symbol,
            "strategy_type": stype,
            "qty": exec_result["qty"],
            "legs": [
                {
                    "action": m["action"],
                    "symbol": m["symbol"],
                    "strike": m.get("strike"),
                    "expiry": m.get("expiry"),
                    "opt_type": m.get("type"),
                    "qty": 1,
                }
                for m in decision["recomputed"]["resolved_legs"]
            ],
            "net_credit_or_debit_per_unit": decision["recomputed"]["net_credit_or_debit_per_unit"],
            "max_loss_usd": decision["recomputed"]["max_loss_usd_per_unit"],
            "client_order_id": exec_result["client_order_id"],
            "order_id": exec_result["order_id"],
            "status": position_status,
            "entry_status": broker_status,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        position.update({
            "quant_signal": quant_gate,
            "strategy_version": quant_gate.get("quant_version"),
            "candidate_id": proposal.get("candidate_id"),
            "monitor_context_enabled": True,
            "indicator_history": [quant_gate["features"]] if quant_gate.get("features") else [],
            "news_risk": news,
        })
        ledger.add_position(latest_data, position)
        ledger.save(latest_data)
    res["action"] = "EXECUTED" if position_status == "OPEN" else "ORDER_SUBMITTED"
    res["execution"] = exec_result
    _stage(stage_callback, "ORDER_SUBMITTED", {
        "status": exec_result.get("status"),
        "action": res["action"],
        "order_id": exec_result.get("order_id"),
    })
    return res
