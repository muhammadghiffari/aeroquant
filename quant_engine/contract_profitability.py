"""Deterministic profitability features for a long option contract."""
from __future__ import annotations

from datetime import datetime, timezone

import config


def _quote_age_seconds(contract, now: datetime) -> float | None:
    stamp = getattr(contract, "quote_timestamp", None)
    if not stamp:
        return None
    try:
        quote_time = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if quote_time.tzinfo is None:
            quote_time = quote_time.replace(tzinfo=timezone.utc)
        return (now - quote_time.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def evaluate_long_option(
    contract,
    *,
    spot: float,
    expected_move_abs: float,
    underlying_probability: float,
    direction: str,
    horizon_bars: int = 1,
    now: datetime | None = None,
) -> dict:
    """Evaluate a conservative ask-to-bid long-option profit scenario.

    The probability is explicitly the underlying signal's probability proxy;
    the returned P&L is contract-specific. It is not called calibrated until
    shadow outcomes supply empirical contract labels.
    """
    direction = str(direction).upper()
    expected_move_abs = float(expected_move_abs or 0)
    probability = float(underlying_probability)
    bid = float(getattr(contract, "bid", 0) or 0)
    ask = float(getattr(contract, "ask", 0) or 0)
    delta = abs(float(getattr(contract, "delta", 0) or 0))
    theta = float(getattr(contract, "theta", 0) or 0)
    reasons: list[str] = []
    expected_type = "call" if direction == "BULLISH" else "put" if direction == "BEARISH" else None
    if expected_type is None or getattr(contract, "opt_type", "") != expected_type:
        reasons.append("contract_direction_mismatch")
    if bid <= 0 or ask <= 0 or ask < bid:
        reasons.append("invalid_quote")
    if float(getattr(contract, "spread_pct", 1.0)) > config.MOMENTUM_MAX_SPREAD_PCT:
        reasons.append("spread_above_limit")
    if not (config.MOMENTUM_MIN_DTE <= int(getattr(contract, "dte", 0)) <= config.MOMENTUM_MAX_DTE):
        reasons.append("dte_out_of_range")
    if not (config.MOMENTUM_MIN_DELTA <= delta <= config.MOMENTUM_MAX_DELTA):
        reasons.append("delta_out_of_range")
    age = _quote_age_seconds(contract, now or datetime.now(timezone.utc))
    if age is None:
        reasons.append("missing_quote_timestamp")
    elif age < 0 or age > config.MOMENTUM_MAX_QUOTE_AGE_SECONDS:
        reasons.append("stale_quote")
    if spot <= 0 or expected_move_abs <= 0:
        reasons.append("invalid_underlying_move")
    if not 0 <= probability <= 1:
        reasons.append("invalid_underlying_probability")

    theta_decay = max(0.0, -theta) * max(1, int(horizon_bars))
    projected_exit_bid = max(0.0, bid + delta * expected_move_abs - theta_decay)
    no_target_exit_bid = max(0.0, bid - theta_decay)
    target_net_pnl = (projected_exit_bid - ask) * 100.0
    no_target_net_pnl = (no_target_exit_bid - ask) * 100.0
    expected_pnl = probability * target_net_pnl + (1.0 - probability) * no_target_net_pnl
    breakeven_move = max(0.0, (ask - bid + theta_decay) / delta) if delta else 0.0
    return {
        "valid": not reasons,
        "reasons": reasons,
        "horizon_bars": int(horizon_bars),
        "entry_price": round(ask, 4),
        "exit_bid": round(bid, 4),
        "quote_age_seconds": round(age, 3) if age is not None else None,
        "theta_decay": round(theta_decay, 4),
        "projected_exit_bid": round(projected_exit_bid, 4),
        "target_net_pnl_usd": round(target_net_pnl, 2),
        "no_target_net_pnl_usd": round(no_target_net_pnl, 2),
        "expected_pnl_usd": round(expected_pnl, 2),
        "breakeven_move_abs": round(breakeven_move, 4),
        "profitable_target_scenario": target_net_pnl > 0,
        "underlying_probability_proxy": round(probability, 4),
        "activity": {
            "last_trade_size": getattr(contract, "last_trade_size", None),
            "bid_size": getattr(contract, "bid_size", None),
            "ask_size": getattr(contract, "ask_size", None),
            "open_interest": getattr(contract, "open_interest", None),
        },
    }
