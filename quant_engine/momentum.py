"""Deterministic single-leg momentum calculations."""
import math

import numpy as np


QUANT_VERSION = "deterministic-single-leg-momentum-v1"


def _wilson_lower_bound(successes: int, sample_size: int, z: float = 1.645) -> float:
    if sample_size <= 0:
        return 0.0
    p = successes / sample_size
    denominator = 1 + z * z / sample_size
    center = p + z * z / (2 * sample_size)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * sample_size)) / sample_size)
    return max(0.0, (center - margin) / denominator)


def estimate_target_probability(returns, required_move, horizon):
    """Estimate forward target probability with a one-sided Wilson bound."""
    horizon = int(horizon)
    values = np.asarray(list(returns), dtype=float)
    if horizon < 1 or len(values) < horizon:
        return {"probability": 0.0, "lower_bound": 0.0, "successes": 0, "sample_size": 0}
    outcomes = []
    for start in range(len(values) - horizon + 1):
        window = values[start : start + horizon]
        if not np.isfinite(window).all():
            continue
        outcomes.append(float(np.prod(1.0 + window) - 1.0))
    target = float(required_move)
    successes = sum(value >= target if target >= 0 else value <= target for value in outcomes)
    sample_size = len(outcomes)
    probability = successes / sample_size if sample_size else 0.0
    return {
        "probability": round(probability, 4),
        "lower_bound": round(_wilson_lower_bound(successes, sample_size), 4),
        "successes": successes,
        "sample_size": sample_size,
        "horizon": horizon,
    }


def build_momentum_signal(bars, *, min_samples=30, horizon=1):
    """Create a conservative directional signal from completed OHLCV bars."""
    closes = np.asarray(bars.get("close", []), dtype=float)
    reasons = []
    if len(closes) < max(int(min_samples) + int(horizon), 22):
        reasons.append("insufficient_samples")
    if not len(closes) or not np.isfinite(closes).all() or np.any(closes <= 0):
        reasons.append("invalid_close_data")
    if reasons:
        return {
            "quant_version": QUANT_VERSION,
            "direction": "WAIT",
            "directional_bias": "WAIT",
            "strategy_type": "WAIT",
            "actionable": False,
            "probability": 0.0,
            "probability_lower_bound": 0.0,
            "sample_size": max(len(closes) - int(horizon) + 1, 0),
            "expected_value_after_costs": None,
            "horizon": int(horizon),
            "data_quality": {"bar_count": len(closes), "valid": False},
            "reasons": reasons,
        }

    fast = float(np.asarray(closes).copy()[-1])
    slow = fast
    alpha_fast = 2 / 10
    alpha_slow = 2 / 22
    for close in closes[1:]:
        fast = alpha_fast * close + (1 - alpha_fast) * fast
        slow = alpha_slow * close + (1 - alpha_slow) * slow
    momentum = float(closes[-1] / closes[-21] - 1.0)
    volumes = np.asarray(bars.get("volume", np.ones(len(closes))), dtype=float)
    vwap = float(np.average(closes, weights=volumes)) if np.all(volumes > 0) else float(closes.mean())
    direction = "BULLISH" if fast > slow and momentum > 0 else "BEARISH" if fast < slow and momentum < 0 else "WAIT"
    target = 0.0001 if direction == "BULLISH" else -0.0001
    probability = estimate_target_probability(np.diff(closes) / closes[:-1], target, horizon)
    if direction == "WAIT":
        reasons.append("directional_alignment_missing")
    elif probability["lower_bound"] < 0.55:
        reasons.append("probability_lower_bound_below_threshold")
    actionable = direction != "WAIT" and not reasons
    return {
        "quant_version": QUANT_VERSION,
        "direction": direction if actionable else "WAIT",
        "directional_bias": direction,
        "strategy_type": (
            {"BULLISH": "LONG_CALL", "BEARISH": "LONG_PUT"}.get(direction, "WAIT")
            if actionable else "WAIT"
        ),
        "actionable": actionable,
        "probability": probability["probability"],
        "probability_lower_bound": probability["lower_bound"],
        "sample_size": probability["sample_size"],
        "expected_value_after_costs": None,
        "horizon": int(horizon),
        "features": {
            "ema_fast": round(fast, 6),
            "ema_slow": round(slow, 6),
            "price": round(float(closes[-1]), 6),
            "vwap": round(vwap, 6),
            "momentum": round(momentum, 6),
        },
        "data_quality": {"bar_count": len(closes), "valid": True},
        "reasons": reasons,
    }


def score_reversal(history, position_direction):
    """Require two completed bars with two of three reversal votes each."""
    rows = list(history or [])[-2:]
    if len(rows) < 2:
        return {"confirmed": False, "bars_confirmed": 0, "votes": 0, "reason": "not_enough_completed_bars"}
    direction = str(position_direction).upper()
    if direction not in {"LONG_CALL", "LONG_PUT"}:
        return {"confirmed": False, "bars_confirmed": 0, "votes": 0, "reason": "unsupported_position"}
    bearish = direction == "LONG_CALL"
    bar_votes = []
    for row in rows:
        checks = [
            row.get("ema_fast", 0) < row.get("ema_slow", 0) if bearish else row.get("ema_fast", 0) > row.get("ema_slow", 0),
            row.get("price", 0) < row.get("vwap", 0) if bearish else row.get("price", 0) > row.get("vwap", 0),
            row.get("momentum", 0) < 0 if bearish else row.get("momentum", 0) > 0,
        ]
        bar_votes.append(sum(bool(check) for check in checks))
    return {
        "confirmed": all(votes >= 2 for votes in bar_votes),
        "bars_confirmed": sum(votes >= 2 for votes in bar_votes),
        "votes": bar_votes[-1],
        "bar_votes": bar_votes,
        "reason": "two_of_three_indicators" if all(votes >= 2 for votes in bar_votes) else "insufficient_reversal_votes",
    }
