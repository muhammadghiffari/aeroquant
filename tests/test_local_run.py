"""Tests for the safe local worker mode."""
from types import SimpleNamespace

import pytest

import config
import orchestrator.pipeline as pipeline


def test_process_symbol_dry_run_never_submits_or_adds_position(monkeypatch):
    quant = {
        "underlying_price": 500.0,
        "volatility": {"iv_rank_proxy_hv_based": 50.0, "hv_iv_spread": 0.01},
        "trend": {"z_score_20d": 0.1},
        "expected_move": {"expected_move_pct": 2.0},
        "earnings": {"earnings_proximity_days": 30},
        "option_chain_summary": {"expiry_used": "2026-09-02"},
        "momentum": {
            "quant_version": "deterministic-single-leg-momentum-v1",
            "direction": "BULLISH",
            "entry_actionable": True,
            "candidates": [{
                "candidate_id": "SPY260902C00495000",
                "symbol": "SPY260902C00495000",
                "strategy_type": "LONG_CALL",
            }],
            "features": {"ema_fast": 501.0, "ema_slow": 500.0, "price": 500.0, "vwap": 499.0, "momentum": 0.01},
        },
    }
    proposal = {
        "symbol": "SPY",
        "candidate_id": "SPY260902C00495000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260902C00495000", "qty": 1}],
    }
    approved = {
        "decision": "APPROVED",
        "recomputed": {
            "resolved_legs": [],
            "net_credit_or_debit_per_unit": 1.0,
            "max_loss_usd_per_unit": 400.0,
        },
    }

    class FakeAgent:
        def run(self, _payload):
            return {}

    class FakeChief:
        def run(self, _payload):
            return dict(proposal)

    class FakeNewsAgent:
        def run(self, _payload):
            return {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.9}

    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(pipeline, "UnderlyingTrendAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "VolatilityAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "NewsEarningsAgent", FakeNewsAgent)
    monkeypatch.setattr(pipeline, "TechnicalManager", FakeAgent)
    monkeypatch.setattr(pipeline, "ContextManager", FakeAgent)
    monkeypatch.setattr(pipeline, "StrategyDecisionAgent", FakeChief)
    monkeypatch.setattr(pipeline, "get_recent_news", lambda _symbol: [{"headline": "No material event"}])
    monkeypatch.setattr(pipeline, "fetch_chain", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "risk_decide", lambda *_args, **_kwargs: approved)
    monkeypatch.setattr(
        pipeline.executor,
        "submit_strategy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run submitted")),
    )
    monkeypatch.setattr(
        pipeline.ledger,
        "add_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run mutated ledger")),
    )

    result = pipeline._process_symbol(
        "SPY",
        SimpleNamespace(buying_power=100000),
        {"positions": [], "daily": {}},
        100000.0,
        {},
        dry_run=True,
    )

    assert result["action"] == "DRY_RUN"
    assert result["dry_run"] is True


def test_run_cycle_dry_run_skips_position_management(monkeypatch, tmp_path):
    data = {"positions": [], "daily": {}}
    seen = {}

    class FakeTradingClient:
        def get_account(self):
            return SimpleNamespace(equity=100000, buying_power=100000)

    monkeypatch.setattr(pipeline.alpaca_client, "trading_client", lambda: FakeTradingClient())
    monkeypatch.setattr(pipeline.ledger, "load", lambda: data)
    monkeypatch.setattr(
        pipeline.position_manager,
        "manage_positions",
        lambda _data: (_ for _ in ()).throw(AssertionError("dry-run managed positions")),
    )
    monkeypatch.setattr(pipeline, "fetch_cycle_context", lambda _symbols: {})
    monkeypatch.setattr(pipeline, "provider_available", lambda: False, raising=False)
    monkeypatch.setattr(
        pipeline,
        "_process_symbol",
        lambda *args, **kwargs: seen.update(dry_run=kwargs["dry_run"]) or {"symbol": args[0]},
    )
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)

    report = pipeline.run_cycle(["SPY"], force=True, dry_run=True)

    assert report["dry_run"] is True
    assert seen["dry_run"] is True
    assert report["position_exits"] == []


def test_run_cycle_blocks_live_execution_when_runtime_preflight_fails(monkeypatch, tmp_path):
    import alerts

    data = {"positions": [], "daily": {}}
    account = SimpleNamespace(id="account-1", equity=100000, buying_power=100000)

    monkeypatch.setattr(pipeline.alpaca_client, "trading_client", lambda: SimpleNamespace(
        get_account=lambda: account,
    ))
    monkeypatch.setattr(pipeline, "configuration_errors", lambda **_kwargs: ["FEATHERLESS_API_KEY is missing"])
    monkeypatch.setattr(pipeline, "account_identity_error", lambda *_args: None)
    monkeypatch.setattr(pipeline, "send_alert", lambda *_args: None)
    monkeypatch.setattr(alerts, "send_alert", lambda *_args, **_kwargs: {"sent": False, "reason": "test"})
    monkeypatch.setattr(pipeline.ledger, "load", lambda: data)
    monkeypatch.setattr(pipeline.config, "REPORTS_DIR", tmp_path)

    report = pipeline.run_cycle(["SPY"], force=True, dry_run=False)

    assert report["blocked"] == "runtime_preflight"
    assert report["runtime_errors"] == ["FEATHERLESS_API_KEY is missing"]


def test_run_cycle_kill_switch_blocks_symbol_processing(monkeypatch, tmp_path):
    data = {"positions": [], "daily": {}}

    class FakeTradingClient:
        def get_account(self):
            return SimpleNamespace(id="account-1", equity=100000, buying_power=100000)

    monkeypatch.setattr(pipeline.alpaca_client, "trading_client", lambda: FakeTradingClient())
    monkeypatch.setattr(pipeline.ledger, "load", lambda: data)
    monkeypatch.setattr(pipeline.ledger, "kill_switch_active", lambda *_args: (True, "test kill"))
    monkeypatch.setattr(pipeline, "send_alert", lambda *_args: None)
    monkeypatch.setattr(pipeline, "fetch_cycle_context", lambda _symbols: {})
    monkeypatch.setattr(pipeline, "_process_symbol", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("kill switch processed a symbol")
    ))
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)

    report = pipeline.run_cycle(["SPY"], force=True, dry_run=True)

    assert report["new_entries_blocked"] == "kill switch: test kill"
    assert report["results"] == [{"symbol": "SPY", "action": "BLOCKED_KILL_SWITCH"}]


def test_run_cycle_does_not_overwrite_entry_commit_with_stale_data(monkeypatch, tmp_path):
    import copy
    from contextlib import nullcontext

    data = {"positions": [], "daily": {}}
    committed = {"positions": [{"id": "position-1", "status": "PENDING_ENTRY"}], "daily": {}}
    current_data = {"value": data}
    saved = []

    monkeypatch.setattr(
        pipeline.alpaca_client,
        "trading_client",
        lambda: SimpleNamespace(get_account=lambda: SimpleNamespace(
            id="account-1", equity=100000, buying_power=100000
        )),
    )
    monkeypatch.setattr(pipeline, "configuration_errors", lambda **_kwargs: [])
    monkeypatch.setattr(pipeline, "telegram_health_check", lambda: (True, "ok"))
    monkeypatch.setattr(pipeline, "account_identity_error", lambda *_args: None)
    monkeypatch.setattr(pipeline.operational_store, "record_cycle", lambda *_args: None)
    monkeypatch.setattr(pipeline.ledger, "load", lambda: current_data["value"])
    monkeypatch.setattr(pipeline.ledger, "save", lambda value: saved.append(copy.deepcopy(value)))
    monkeypatch.setattr(pipeline.ledger, "ledger_transaction", lambda: nullcontext())
    monkeypatch.setattr(pipeline.position_manager, "manage_positions", lambda _data: [])
    monkeypatch.setattr(pipeline, "fetch_cycle_context", lambda _symbols: {})
    def fake_process(*_args, **_kwargs):
        current_data["value"] = committed
        pipeline.ledger.save(committed)
        return {"symbol": "SPY", "action": "ORDER_SUBMITTED"}

    monkeypatch.setattr(pipeline, "_process_symbol", fake_process)
    monkeypatch.setattr(pipeline, "notify_cycle_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "configuration_errors", lambda **_kwargs: [])
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "EVALUATION_ENABLED", False)

    pipeline.run_cycle(["SPY"], force=True)

    assert saved[-1] == committed


def test_run_cycle_emits_ordered_stage_events_per_symbol(monkeypatch, tmp_path):
    stage_ids = []

    monkeypatch.setattr(
        pipeline.alpaca_client,
        "trading_client",
        lambda: SimpleNamespace(get_account=lambda: SimpleNamespace(
            id="account-1", equity=100000, buying_power=100000
        )),
    )
    monkeypatch.setattr(pipeline, "configuration_errors", lambda **_kwargs: [])
    monkeypatch.setattr(pipeline, "telegram_health_check", lambda: (True, "ok"))
    monkeypatch.setattr(pipeline, "account_identity_error", lambda *_args: None)
    monkeypatch.setattr(pipeline.operational_store, "record_cycle", lambda *_args: None)
    monkeypatch.setattr(pipeline.ledger, "load", lambda: {"positions": [], "daily": {}})
    monkeypatch.setattr(pipeline.ledger, "save", lambda _value: None)
    monkeypatch.setattr(pipeline.ledger, "ledger_transaction", lambda: __import__("contextlib").nullcontext())
    monkeypatch.setattr(pipeline.position_manager, "manage_positions", lambda _data: [])
    monkeypatch.setattr(pipeline, "fetch_cycle_context", lambda _symbols: {})
    monkeypatch.setattr(pipeline, "notify_cycle_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "EVALUATION_ENABLED", False)
    monkeypatch.setattr(pipeline, "emit_stage", lambda cycle_id, symbol, stage, sequence, _details: stage_ids.append(
        f"{cycle_id}:{symbol}:{stage}:{sequence}"
    ) or {"sent": True, "event_id": stage_ids[-1]})

    def fake_process(symbol, *_args, **kwargs):
        stage = kwargs["stage_callback"]
        stage("QUANT_COMPLETED", {"status": "complete"})
        stage("STRATEGY_DECIDED", {"status": "complete"})
        stage("RISK_DECIDED", {"status": "rejected", "decision": "REJECTED"})
        stage("ORDER_SUBMITTED", {"status": "skipped", "action": "REJECTED"})
        return {"symbol": symbol, "action": "REJECTED", "rejection_reason": "test"}

    monkeypatch.setattr(pipeline, "_process_symbol", fake_process)
    def fake_graph(symbol, callback):
        return callback(symbol)

    monkeypatch.setattr(pipeline, "run_symbol_graph", fake_graph)

    report = pipeline.run_cycle(["SPY"], force=True)

    assert report["stage_events"]
    assert stage_ids == [
        f"{report['cycle_id']}:SPY:CYCLE_STARTED:0",
        f"{report['cycle_id']}:SPY:QUANT_COMPLETED:1",
        f"{report['cycle_id']}:SPY:STRATEGY_DECIDED:2",
        f"{report['cycle_id']}:SPY:RISK_DECIDED:3",
        f"{report['cycle_id']}:SPY:ORDER_SUBMITTED:4",
        f"{report['cycle_id']}:SPY:CYCLE_COMPLETED:5",
    ]


def test_run_cycle_blocks_when_telegram_health_fails_before_analysis(monkeypatch, tmp_path):
    processed = []
    monkeypatch.setattr(
        pipeline.alpaca_client,
        "trading_client",
        lambda: SimpleNamespace(get_account=lambda: SimpleNamespace(
            id="account-1", equity=100000, buying_power=100000
        )),
    )
    monkeypatch.setattr(pipeline, "configuration_errors", lambda **_kwargs: [])
    monkeypatch.setattr(pipeline, "telegram_health_check", lambda: (False, "telegram getChat failed"))
    monkeypatch.setattr(pipeline, "account_identity_error", lambda *_args: None)
    monkeypatch.setattr(pipeline, "send_alert", lambda *_args: None)
    monkeypatch.setattr(pipeline.ledger, "load", lambda: {"positions": [], "daily": {}})
    monkeypatch.setattr(pipeline, "notify_cycle_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "run_symbol_graph", lambda *_args: processed.append(True))
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)

    report = pipeline.run_cycle(["SPY"], force=True)

    assert report["blocked"] == "runtime_preflight"
    assert report["runtime_errors"] == ["TELEGRAM_HEALTHCHECK_FAILED: telegram getChat failed"]
    assert processed == []


def test_monitor_once_manages_and_persists_positions(monkeypatch):
    import main

    data = {"positions": [], "daily": {}}
    calls = {"saved": 0}
    monkeypatch.setattr("execution.ledger.load", lambda: data)
    monkeypatch.setattr("execution.position_manager.manage_positions", lambda value: [{"id": "exit-1"}])
    monkeypatch.setattr("execution.ledger.save", lambda value: calls.update(saved=calls["saved"] + 1))

    assert main._monitor_once() == [{"id": "exit-1"}]
    assert calls["saved"] == 1


def test_monitor_once_serializes_ledger_mutation(monkeypatch):
    import main
    from execution import ledger, position_manager

    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *_args):
            events.append("exit")

    monkeypatch.setattr(ledger, "ledger_transaction", lambda: FakeLock())
    monkeypatch.setattr(ledger, "load", lambda: events.append("load") or {"positions": [], "daily": {}})
    monkeypatch.setattr(
        position_manager,
        "manage_positions",
        lambda _data: events.append("manage") or [],
    )
    monkeypatch.setattr(ledger, "save", lambda _data: events.append("save"))

    main._monitor_once()

    assert events == ["enter", "load", "manage", "save", "exit"]


def test_monitor_dry_run_does_not_manage_or_save(monkeypatch):
    monkeypatch.setattr("execution.position_manager.manage_positions", lambda _data: (_ for _ in ()).throw(
        AssertionError("dry-run monitor submitted an exit")
    ))
    monkeypatch.setattr("execution.ledger.save", lambda _data: (_ for _ in ()).throw(
        AssertionError("dry-run monitor mutated ledger")
    ))

    import main

    assert main._monitor_once(dry_run=True) == []


def test_process_symbol_skips_ticker_with_active_position(monkeypatch):
    monkeypatch.setattr(
        "quant_engine.engine.build_quant_report",
        lambda _symbol: (_ for _ in ()).throw(AssertionError("active ticker fetched market data")),
    )

    result = pipeline._process_symbol(
        "SPY",
        SimpleNamespace(buying_power=100000),
        {"positions": [{"underlying": "SPY", "status": "OPEN"}], "daily": {}},
        100000.0,
    )

    assert result == {"symbol": "SPY", "action": "SKIPPED_ACTIVE_POSITION"}


def test_process_symbol_skips_all_llm_agents_when_quant_gate_is_wait(monkeypatch):
    quant = {
        "underlying_price": 500.0,
        "momentum": {
            "direction": "WAIT",
            "strategy_type": "WAIT",
            "entry_actionable": False,
            "reasons": ["probability_lower_bound_below_threshold"],
            "candidates": [],
        },
    }
    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(
        pipeline,
        "UnderlyingTrendAgent",
        lambda: (_ for _ in ()).throw(AssertionError("LLM called before Quant gate")),
    )

    result = pipeline._process_symbol(
        "SPY",
        SimpleNamespace(buying_power=100000),
        {"positions": [], "daily": {}},
        100000.0,
    )

    assert result["action"] == "WAIT_QUANT_GATE"
    assert result["quant_gate"]["reasons"] == ["probability_lower_bound_below_threshold"]


def test_process_symbol_preserves_confidence_wait_see_before_llm(monkeypatch):
    quant = {
        "underlying_price": 500.0,
        "momentum": {
            "direction": "BULLISH",
            "strategy_type": "WAIT",
            "entry_actionable": False,
            "confidence": {
                "state": "WAIT_SEE",
                "reasons": ["setup_lower_bound_below_threshold"],
            },
            "reasons": ["setup_lower_bound_below_threshold"],
            "candidates": [],
        },
    }
    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(
        pipeline,
        "UnderlyingTrendAgent",
        lambda: (_ for _ in ()).throw(AssertionError("LLM called before confidence gate")),
    )

    result = pipeline._process_symbol(
        "SPY",
        SimpleNamespace(buying_power=100000),
        {"positions": [], "daily": {}},
        100000.0,
    )

    assert result["action"] == "WAIT_SEE"
    assert result["confidence"]["state"] == "WAIT_SEE"


def test_process_symbol_records_shadow_snapshot_before_wait(monkeypatch):
    monkeypatch.setattr(config, "SHADOW_ANALYSIS_ENABLED", True, raising=False)
    quant = {
        "underlying_price": 500.0,
        "momentum": {
            "entry_actionable": False,
            "confidence": {"state": "AMBER", "reasons": ["no_matched_contract_outcomes"]},
            "shadow_context": {"bar_count": 100, "timeframe": "1H"},
            "daily_shadow_context": {"bar_count": 200, "timeframe": "1D"},
            "shadow_candidates": [{"symbol": "SPY260919C00500000"}],
        },
    }
    seen = []
    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(
        pipeline.shadow_store,
        "process_snapshot", lambda *args: seen.append(args) or {
            "recorded": 1 if args[1].get("timeframe") != "1D" else 0,
            "resolved": 0,
        },
    )

    result = pipeline._process_symbol(
        "SPY",
        SimpleNamespace(buying_power=100000),
        {"positions": [], "daily": {}},
        100000.0,
        dry_run=False,
    )

    assert result["action"] == "WAIT_SEE"
    assert [args[1]["timeframe"] for args in seen] == ["1H", "1D"]
    assert result["shadow"] == {"recorded": 1, "resolved": 0}


def test_process_symbol_runs_analysis_only_for_directional_shadow_candidates(monkeypatch):
    monkeypatch.setattr(config, "SHADOW_ANALYSIS_ENABLED", True, raising=False)
    candidate = {
        "candidate_id": "SPY260919C00500000",
        "symbol": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "profitability": {"valid": True, "expected_pnl_usd": 12.0},
        "contract_confidence": {"state": "WAIT_DATA"},
    }
    quant = {
        "underlying_price": 500.0,
        "volatility": {"iv_rank_proxy_hv_based": 50.0, "hv_iv_spread": 0.01},
        "trend": {"z_score_20d": 0.1},
        "expected_move": {"expected_move_pct": 2.0},
        "earnings": {"earnings_proximity_days": 30},
        "momentum": {
            "direction": "WAIT",
            "directional_bias": "BULLISH",
            "entry_actionable": False,
            "confidence": {"state": "WAIT_DATA", "reasons": ["no_matched_contract_outcomes"]},
            "shadow_context": {"bar_count": 100, "timeframe": "1H"},
            "shadow_candidates": [candidate],
            "candidates": [],
        },
    }
    calls = {"agents": 0}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, _payload):
            calls["agents"] += 1
            return {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.9}

    class FakeChief(FakeAgent):
        def run(self, _payload):
            calls["agents"] += 1
            return {
                "symbol": "SPY", "candidate_id": candidate["candidate_id"],
                "strategy_type": "LONG_CALL", "rationale": "shadow analysis",
                "legs": [{"action": "BUY", "symbol": candidate["symbol"], "qty": 1}],
            }

    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(pipeline, "UnderlyingTrendAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "VolatilityAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "NewsEarningsAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "TechnicalManager", FakeAgent)
    monkeypatch.setattr(pipeline, "ContextManager", FakeAgent)
    monkeypatch.setattr(pipeline, "StrategyDecisionAgent", FakeChief)
    monkeypatch.setattr(pipeline, "get_recent_news", lambda _symbol: [{"headline": "No material event"}])
    monkeypatch.setattr(pipeline.shadow_store, "process_snapshot", lambda *_args: {"recorded": 1, "resolved": 0})

    result = pipeline._process_symbol(
        "SPY", SimpleNamespace(buying_power=100000), {"positions": [], "daily": {}}, 100000.0
    )

    assert result["action"] == "SHADOW_ONLY"
    assert result["analysis_only"] is True
    assert result["execution"] is None
    assert calls["agents"] > 0


def test_process_symbol_keeps_llm_in_underlying_history_proxy_lane(monkeypatch):
    candidate = {
        "candidate_id": "SPY260919C00500000",
        "symbol": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "profitability": {"valid": True, "expected_pnl_usd": 12.0},
        "contract_confidence": {"state": "WAIT_DATA"},
    }
    quant = {
        "underlying_price": 500.0,
        "volatility": {"iv_rank_proxy_hv_based": 50.0, "hv_iv_spread": 0.01},
        "trend": {"z_score_20d": 0.1},
        "expected_move": {"expected_move_pct": 2.0},
        "earnings": {"earnings_proximity_days": 30},
        "momentum": {
            "direction": "BULLISH",
            "entry_actionable": True,
            "entry_mode": "UNDERLYING_HISTORY_PROXY",
            "confidence": {"state": "GREEN_PROXY", "direction": "BULLISH", "source": "stock_bars"},
            "candidates": [candidate],
            "shadow_candidates": [candidate],
        },
    }
    calls = {"agents": 0, "shadow": 0}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, _payload):
            calls["agents"] += 1
            return {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.9}

    class FakeChief(FakeAgent):
        def run(self, _payload):
            calls["agents"] += 1
            return {
                "symbol": "SPY", "candidate_id": candidate["candidate_id"],
                "strategy_type": "LONG_CALL", "rationale": "history proxy",
                "legs": [{"action": "BUY", "symbol": candidate["symbol"], "qty": 1}],
            }

    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(pipeline, "UnderlyingTrendAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "VolatilityAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "NewsEarningsAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "TechnicalManager", FakeAgent)
    monkeypatch.setattr(pipeline, "ContextManager", FakeAgent)
    monkeypatch.setattr(pipeline, "StrategyDecisionAgent", FakeChief)
    monkeypatch.setattr(pipeline, "get_recent_news", lambda _symbol: [{"headline": "No material event"}])
    monkeypatch.setattr(pipeline, "fetch_chain", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        config,
        "SHADOW_ANALYSIS_ENABLED",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline.shadow_store,
        "process_snapshot",
        lambda *_args: calls.update(shadow=calls["shadow"] + 1) or {"recorded": 1, "resolved": 0},
    )
    monkeypatch.setattr(pipeline, "risk_decide", lambda *_args, **_kwargs: {"decision": "REJECTED"})

    result = pipeline._process_symbol(
        "SPY", SimpleNamespace(buying_power=100000), {"positions": [], "daily": {}}, 100000.0
    )

    assert result["action"] == "REJECTED"
    assert result["reports"]["proposal"]["candidate_id"] == candidate["candidate_id"]
    assert calls["agents"] > 0
    assert calls["shadow"] == 0


def test_news_gate_rejects_missing_or_low_confidence_analysis():
    assert pipeline._news_gate_passes({}, ["headline"]) is False
    assert pipeline._news_gate_passes(
        {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.49},
        ["headline"],
    ) is False


def test_news_gate_requires_grounded_headlines_and_confidence():
    assert pipeline._news_gate_passes(
        {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.80},
        ["headline"],
    ) is True
    assert pipeline._news_gate_passes(
        {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.80},
        [],
    ) is False


def test_main_rejects_removed_scalp_entry_mode(monkeypatch):
    import sys
    import main

    monkeypatch.setattr(sys, "argv", ["main.py", "--scalp-once"])

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 2


def test_process_symbol_persists_order_intent_before_submission(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.json")
    quant = {
        "underlying_price": 500.0,
        "volatility": {"iv_rank_proxy_hv_based": 50.0, "hv_iv_spread": 0.01},
        "trend": {"z_score_20d": 0.1},
        "expected_move": {"expected_move_pct": 2.0},
        "earnings": {"earnings_proximity_days": 30},
        "option_chain_summary": {"expiry_used": "2026-09-02"},
        "momentum": {
            "quant_version": "deterministic-single-leg-momentum-v1",
            "direction": "BULLISH",
            "entry_actionable": True,
            "candidates": [{
                "candidate_id": "SPY260902C00495000",
                "symbol": "SPY260902C00495000",
                "strategy_type": "LONG_CALL",
            }],
            "features": {"ema_fast": 501.0, "ema_slow": 500.0, "price": 500.0, "vwap": 499.0, "momentum": 0.01},
        },
    }
    proposal = {
        "symbol": "SPY", "strategy_type": "LONG_CALL",
        "candidate_id": "SPY260902C00495000",
        "legs": [{"action": "BUY", "symbol": "SPY260902C00495000", "qty": 1}],
    }
    decision = {
        "decision": "APPROVED", "adjusted_qty": 1,
        "recomputed": {"resolved_legs": [{"action": "BUY", "symbol": "SPY260902C00495000"}],
                       "net_credit_or_debit_per_unit": -1.0, "max_loss_usd_per_unit": 100.0},
    }
    events = []
    data = {"positions": [], "daily": {}}
    lock_events = []

    class FakeLock:
        def __enter__(self):
            lock_events.append("enter")

        def __exit__(self, *_args):
            lock_events.append("exit")

    class FakeAgent:
        def run(self, _payload): return {}

    class FakeChief:
        def run(self, _payload): return dict(proposal)

    class FakeNewsAgent:
        def run(self, _payload):
            return {"event_risk": "LOW", "sentiment": "NEUTRAL", "confidence": 0.9}

    monkeypatch.setattr("quant_engine.engine.build_quant_report", lambda _symbol: quant)
    monkeypatch.setattr(pipeline, "UnderlyingTrendAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "VolatilityAgent", FakeAgent)
    monkeypatch.setattr(pipeline, "NewsEarningsAgent", FakeNewsAgent)
    monkeypatch.setattr(pipeline, "TechnicalManager", FakeAgent)
    monkeypatch.setattr(pipeline, "ContextManager", FakeAgent)
    monkeypatch.setattr(pipeline, "StrategyDecisionAgent", FakeChief)
    monkeypatch.setattr(pipeline, "get_recent_news", lambda _symbol: [{"headline": "No material event"}])
    monkeypatch.setattr(pipeline, "fetch_chain", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "risk_decide", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(pipeline.ledger, "ledger_transaction", lambda: FakeLock())
    monkeypatch.setattr(pipeline.ledger, "load", lambda: data)
    monkeypatch.setattr(
        pipeline.operational_store, "create_order_intent",
        lambda *_args: events.append("intent"),
    )
    monkeypatch.setattr(
        pipeline.operational_store, "record_order_ack",
        lambda *_args: events.append("ack"),
    )
    monkeypatch.setattr(
        pipeline.executor, "submit_strategy",
        lambda *_args, client_order_id: events.append("submit") or {
            "qty": 1, "order_id": "order-1", "client_order_id": client_order_id, "status": "accepted"
        },
    )
    monkeypatch.setattr(pipeline.ledger, "save", lambda _data: None)

    result = pipeline._process_symbol(
        "SPY", SimpleNamespace(id="account-1", buying_power=100000), data,
        100000.0, {}, cycle_id="cycle-1", account_id="account-1",
    )

    assert result["action"] == "ORDER_SUBMITTED"
    assert events == ["intent", "submit", "ack"]
    assert lock_events == ["enter", "exit"]
    assert data["positions"][0]["status"] == "PENDING_ENTRY"
    assert data["positions"][0]["candidate_id"] == "SPY260902C00495000"
    assert data["positions"][0]["monitor_context_enabled"] is True
