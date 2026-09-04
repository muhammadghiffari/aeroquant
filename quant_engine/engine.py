"""Quant Engine orchestrator -> quant_report (numeric, 'Opsi B' format)."""
import logging
from datetime import datetime, timezone

import config
from data_engine.news_data import estimate_earnings_proximity
from data_engine.option_data import (
    atm_contracts,
    build_candidate_whitelist,
    build_chain_summary,
    build_shadow_candidate_list,
    fetch_chain,
    nearest_expiry,
)
from data_engine.stock_data import get_daily_bars, get_hourly_bars
from execution import shadow_store
from execution.shadow_store import HOURLY_TIMEFRAME, DAILY_TIMEFRAME, bar_count_from_timestamp
from quant_engine.contract_confidence import build_contract_confidence
from quant_engine.contract_profitability import evaluate_long_option
from quant_engine.entry_confidence import build_entry_confidence
from quant_engine.expected_move import expected_move
from quant_engine.confidence import build_confidence_signal
from quant_engine.momentum import build_momentum_signal
from quant_engine.probability import skew_metrics
from quant_engine.trend_score import trend_metrics
from quant_engine.volatility_metrics import volatility_metrics

log = logging.getLogger(__name__)


def _contract_by_symbol(chain, symbol):
    return next((contract for contract in chain if contract.symbol == symbol), None)


def _resolved_outcomes(timeframe: str) -> list[dict]:
    try:
        return shadow_store.resolved_outcomes(timeframe=timeframe)
    except TypeError:
        return shadow_store.resolved_outcomes()


def _pending_quotes(chain, pending_reader, symbol: str, timeframe: str) -> dict:
    if pending_reader is None:
        return {}
    try:
        rows = pending_reader(symbol, timeframe=timeframe)
    except TypeError:
        rows = pending_reader(symbol)
    pending_symbols = {row["contract_symbol"] for row in rows}
    return {
        contract.symbol: {"bid": contract.bid}
        for contract in chain
        if contract.symbol in pending_symbols
    }


def build_quant_report(symbol: str) -> dict | None:
    """All numbers for one symbol; returns None when data is unusable."""
    symbol = symbol.upper()
    daily_bars = get_daily_bars(symbol, days=400)
    bars = get_hourly_bars(symbol, days=60)
    closes = [float(c) for c in bars["close"].tolist()]
    daily_closes = [float(c) for c in daily_bars["close"].tolist()]
    spot = closes[-1]

    chain = fetch_chain(
        symbol,
        min_dte=config.MOMENTUM_MIN_DTE,
        max_dte=config.MOMENTUM_MAX_DTE,
        max_spread_pct=config.MOMENTUM_MAX_SPREAD_PCT,
    )
    if not chain:
        log.warning("%s: empty option chain, skipping", symbol)
        return None

    expiry = nearest_expiry(chain, target_dte=7)
    summary = build_chain_summary(chain, spot)
    call, put = atm_contracts(chain, spot, expiry) if expiry else (None, None)

    atm_iv = summary.get("median_iv_expiry")
    option_move = expected_move(spot, call, put)
    momentum = build_momentum_signal(
        bars,
        min_samples=config.MOMENTUM_MIN_SAMPLES,
        horizon=config.MOMENTUM_HORIZON,
    )
    momentum["expected_move_abs"] = option_move.get("expected_move_abs")
    momentum["audit"] = {
        "unconditional_probability": momentum.get("probability"),
        "unconditional_lower_bound": momentum.get("probability_lower_bound"),
        "sample_size": momentum.get("sample_size"),
        "direction": momentum.get("directional_bias", momentum.get("direction")),
        "actionable": momentum.get("actionable"),
    }
    confidence = build_confidence_signal(
        bars,
        min_samples=config.MOMENTUM_MIN_SAMPLES,
        horizon=config.MOMENTUM_HORIZON,
    )
    direction = str(confidence.get("direction", "WAIT")).upper()
    outcomes = _resolved_outcomes(HOURLY_TIMEFRAME)
    shadow_candidates = build_shadow_candidate_list(
        chain, direction, max_candidates=config.SHADOW_MAX_CANDIDATES
    )
    pending_reader = getattr(shadow_store, "pending_observations", None)
    pending_quotes = _pending_quotes(chain, pending_reader, symbol, HOURLY_TIMEFRAME)
    daily_pending_quotes = _pending_quotes(chain, pending_reader, symbol, DAILY_TIMEFRAME)
    underlying_probability = float(
        confidence.get("setup_probability") or momentum.get("probability") or 0.0
    )
    expected_move_abs = float(option_move.get("expected_move_abs") or 0.0)
    green_candidates = []
    for item in shadow_candidates:
        contract = _contract_by_symbol(chain, item["symbol"])
        if contract is None:
            continue
        profitability = evaluate_long_option(
            contract,
            spot=spot,
            expected_move_abs=expected_move_abs,
            underlying_probability=underlying_probability,
            direction=direction,
            horizon_bars=config.CONTRACT_CONFIDENCE_HORIZON,
        )
        contract_confidence = build_contract_confidence(
            outcomes,
            direction=direction,
            volatility_regime=confidence.get("volatility_regime"),
            dte=contract.dte,
            delta=contract.delta or 0.0,
            timeframe=HOURLY_TIMEFRAME,
        )
        item["profitability"] = profitability
        item["contract_confidence"] = contract_confidence
        if (
            contract_confidence["state"] == "GREEN"
            and profitability["valid"]
            and profitability["expected_pnl_usd"] > 0
        ):
            green_candidates.append((contract, item))

    entry_confidence = build_entry_confidence(confidence)
    candidate_items = [(contract, item, "CONTRACT_HISTORY") for contract, item in green_candidates]
    if not candidate_items and direction in {"BULLISH", "BEARISH"}:
        candidate_items = [
            (contract, item, "UNDERLYING_HISTORY_PROXY")
            for item in shadow_candidates
            if (item.get("profitability") or {}).get("valid")
            for contract in [_contract_by_symbol(chain, item["symbol"])]
            if contract is not None
        ]

    candidates = []
    for contract, item, source in candidate_items:
        confidence_lower_bound = (
            item["contract_confidence"]["lower_bound"]
            if source == "CONTRACT_HISTORY"
            else entry_confidence["lower_bound"]
        )
        candidate_signal = {
            "direction": direction,
            "actionable": True,
            "probability_lower_bound": confidence_lower_bound,
            "expected_move_abs": expected_move_abs,
        }
        if source == "UNDERLYING_HISTORY_PROXY":
            candidate_signal.update({
                "probability": entry_confidence["probability"],
                "probability_floor": config.MOMENTUM_PROXY_MIN_PROBABILITY,
                "probability_basis": "stock_history_setup_probability",
                "historical_confidence_advisory": True,
                "require_positive_ev": False,
            })
        candidates.extend(
            build_candidate_whitelist([contract], candidate_signal, spot, max_candidates=1)
        )
    for candidate in candidates:
        shadow = next(item for item in shadow_candidates if item["symbol"] == candidate["symbol"])
        candidate["profitability"] = shadow["profitability"]
        candidate["contract_confidence"] = shadow["contract_confidence"]
        candidate["confidence_source"] = (
            "contract_history"
            if any(contract.symbol == candidate["symbol"] for contract, _, source in candidate_items if source == "CONTRACT_HISTORY")
            else "underlying_history_proxy"
        )
    candidates.sort(key=lambda item: (-item["expected_value_after_costs"], item["spread_pct"]))
    if green_candidates:
        final_confidence = green_candidates[0][1]["contract_confidence"]
    elif shadow_candidates:
        final_confidence = shadow_candidates[0]["contract_confidence"]
    else:
        final_confidence = {
            "confidence_version": "option-profit-calibrated-v1",
            "state": "WAIT_DATA",
            "actionable": False,
            "direction": direction,
            "reasons": ["underlying_direction_unavailable"],
        }
    final_confidence["underlying"] = {
        "state": confidence.get("state"),
        "direction": direction,
        "setup_lower_bound": confidence.get("setup_lower_bound"),
        "horizon_alignment": confidence.get("horizon_alignment"),
    }
    momentum["underlying_confidence"] = confidence
    momentum["contract_confidence"] = final_confidence
    momentum["entry_confidence"] = entry_confidence
    momentum["shadow_candidates"] = shadow_candidates
    bar_timestamp = bars.index[-1]
    momentum["shadow_context"] = {
        "bar_count": bar_count_from_timestamp(bar_timestamp, HOURLY_TIMEFRAME),
        "timeframe": HOURLY_TIMEFRAME,
        "bar_timestamp": str(bar_timestamp),
        "pending_quotes": pending_quotes,
    }
    daily_timestamp = daily_bars.index[-1]
    momentum["daily_shadow_context"] = {
        "bar_count": bar_count_from_timestamp(daily_timestamp, DAILY_TIMEFRAME),
        "timeframe": DAILY_TIMEFRAME,
        "bar_timestamp": str(daily_timestamp),
        "pending_quotes": daily_pending_quotes,
    }
    momentum["candidates"] = candidates
    momentum["entry_actionable"] = bool(candidates)
    uses_contract_history = any(source == "CONTRACT_HISTORY" for _, _, source in candidate_items)
    momentum["entry_mode"] = (
        "CONTRACT_HISTORY" if uses_contract_history
        else "UNDERLYING_HISTORY_PROXY" if candidates else "NONE"
    )
    # Candidates are the effective executable gate when historical confidence
    # is advisory; expose the matching direction to downstream strategy/risk.
    if candidates and direction in {"BULLISH", "BEARISH"}:
        momentum["direction"] = direction
        momentum["strategy_type"] = {
            "BULLISH": "LONG_CALL", "BEARISH": "LONG_PUT"
        }[direction]
        momentum["actionable"] = True
    momentum["confidence"] = final_confidence if uses_contract_history else entry_confidence
    if not candidates and green_candidates:
        momentum.setdefault("entry_reasons", []).append("no_positive_ev_candidate")

    report: dict = {
        "source": "QuantEngine",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "underlying_price": round(spot, 2),
        "volatility": volatility_metrics(closes, atm_iv),
        "expected_move": option_move,
        "trend": trend_metrics(closes),
        "daily_context": {
            "timeframe": DAILY_TIMEFRAME,
            "bars": len(daily_bars),
            "first_bar": str(daily_bars.index[0]),
            "last_bar": str(daily_timestamp),
            "trend": trend_metrics(daily_closes),
        },
        "momentum": momentum,
        "skew": skew_metrics(summary),
        "option_chain_summary": summary,
        "earnings": estimate_earnings_proximity(symbol),
        "data_quality": {
            "iv_source": "option_chain_snapshot",
            "iv_rank_method": "hv_proxy (no historical IV series available)",
            "n_tradable_contracts": len(chain),
            "entry_analysis_timeframe": HOURLY_TIMEFRAME,
            "entry_confidence_source": entry_confidence["source"],
        },
    }
    return report
