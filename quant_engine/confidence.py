"""Causal setup-conditioned confidence calculations."""
import numpy as np

import config
from quant_engine.momentum import _wilson_lower_bound


CONFIDENCE_VERSION = "setup-conditioned-momentum-v1"


def _close_array(bars) -> np.ndarray:
    closes = np.asarray(bars.get("close", []), dtype=float)
    return closes


def _ema_last(values: np.ndarray, span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    ema = float(values[0])
    for value in values[1:]:
        ema = alpha * float(value) + (1.0 - alpha) * ema
    return ema


def _endpoint_features(closes: np.ndarray, endpoint: int) -> dict | None:
    prefix = closes[: endpoint + 1]
    if len(prefix) < 61 or not np.isfinite(prefix).all() or np.any(prefix <= 0):
        return None
    ema_fast = _ema_last(prefix, 10)
    ema_slow = _ema_last(prefix, 22)
    momentum_20 = float(prefix[-1] / prefix[-21] - 1.0)
    returns = np.diff(np.log(prefix[-21:]))
    realized_vol = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    if ema_fast > ema_slow and momentum_20 > 0:
        direction = "BULLISH"
    elif ema_fast < ema_slow and momentum_20 < 0:
        direction = "BEARISH"
    else:
        direction = "WAIT"
    return {
        "direction": direction,
        "realized_vol": realized_vol,
        "returns": {
            horizon: float(prefix[-1] / prefix[-horizon - 1] - 1.0)
            for horizon in (5, 20, 60)
        },
    }


def _regime(volatility: float, prior_volatility: list[float]) -> str | None:
    if not prior_volatility or not np.isfinite(volatility):
        return None
    median = float(np.median(prior_volatility))
    return "HIGH" if volatility >= median else "LOW"


def confirm_horizons(closes, direction: str) -> dict:
    """Return signs and the 2-of-3 result for 5, 20, and 60 bars."""
    values = np.asarray(list(closes), dtype=float)
    direction = str(direction).upper()
    horizons = {}
    if direction not in {"BULLISH", "BEARISH"} or len(values) < 61:
        return {"horizons": horizons, "agreeing": 0, "total": 3, "passed": False}
    for period in (5, 20, 60):
        change = float(values[-1] / values[-period - 1] - 1.0)
        sign = "BULLISH" if change > 0 else "BEARISH" if change < 0 else "FLAT"
        horizons[str(period)] = sign
    agreeing = sum(value == direction for value in horizons.values())
    return {
        "horizons": horizons,
        "agreeing": agreeing,
        "total": 3,
        "passed": agreeing >= 2,
    }


def build_confidence_signal(
    bars,
    *,
    min_samples: int = 30,
    horizon: int | None = None,
    as_of_index: int | None = None,
) -> dict:
    """Return a causal conditioned confidence decision."""
    horizon = config.MOMENTUM_HORIZON if horizon is None else int(horizon)
    closes = _close_array(bars)
    if as_of_index is None:
        endpoint = len(closes) - 1
    else:
        endpoint = int(as_of_index)
    if endpoint < 0 or endpoint >= len(closes):
        endpoint = -1
    values = closes[: endpoint + 1] if endpoint >= 0 else np.asarray([], dtype=float)
    base = {
        "confidence_version": CONFIDENCE_VERSION,
        "state": "WAIT_DATA",
        "direction": "WAIT",
        "setup_probability": 0.0,
        "setup_lower_bound": 0.0,
        "setup_successes": 0,
        "setup_sample_size": 0,
        "volatility_regime": None,
        "horizon_alignment": {"horizons": {}, "agreeing": 0, "total": 3, "passed": False},
        "confidence_score": 0.0,
        "actionable": False,
        "horizon": horizon,
        "reasons": [],
    }
    if len(values) < 61 or not np.isfinite(values).all() or np.any(values <= 0):
        base["reasons"] = ["invalid_or_insufficient_history"]
        return base

    current = _endpoint_features(values, len(values) - 1)
    if current is None:
        base["reasons"] = ["invalid_or_insufficient_history"]
        return base
    base["direction"] = current["direction"]
    alignment = confirm_horizons(values, current["direction"])
    base["horizon_alignment"] = alignment
    prior_volatility = []
    historical = []
    for index in range(61, len(values)):
        feature = _endpoint_features(values, index)
        if feature is None:
            continue
        regime = _regime(feature["realized_vol"], prior_volatility)
        if index + int(horizon) < len(values) and regime is not None:
            historical.append((feature["direction"], regime, feature["realized_vol"], index))
        prior_volatility.append(feature["realized_vol"])

    current_regime = _regime(current["realized_vol"], prior_volatility[:-1] or prior_volatility)
    base["volatility_regime"] = current_regime
    if current["direction"] == "WAIT":
        base["state"] = "WAIT_SEE"
        base["reasons"] = ["directional_alignment_missing"]
        return base
    if current_regime is None:
        base["reasons"] = ["volatility_regime_unavailable"]
        return base

    successes = 0
    sample_size = 0
    target = 0.0001 if current["direction"] == "BULLISH" else -0.0001
    for direction, regime, _volatility, index in historical:
        if direction != current["direction"] or regime != current_regime:
            continue
        sample_size += 1
        forward_return = float(values[index + int(horizon)] / values[index] - 1.0)
        successes += int(forward_return >= target if target >= 0 else forward_return <= target)

    base["setup_successes"] = successes
    base["setup_sample_size"] = sample_size
    if sample_size:
        probability = successes / sample_size
        lower_bound = _wilson_lower_bound(successes, sample_size)
        base["setup_probability"] = round(probability, 4)
        base["setup_lower_bound"] = round(lower_bound, 4)
        base["confidence_score"] = round(
            min(lower_bound, alignment["agreeing"] / 3.0), 4
        )
    reasons = []
    if sample_size < int(min_samples):
        reasons.append("insufficient_conditioned_samples")
    if base["setup_lower_bound"] < config.MOMENTUM_MIN_PROBABILITY_LB:
        reasons.append("setup_lower_bound_below_threshold")
    if not alignment["passed"]:
        reasons.append("horizon_alignment_below_threshold")
    if reasons:
        base["state"] = "WAIT_SEE"
        base["reasons"] = reasons
        return base
    base["state"] = "ENTER_CONFIRMED"
    base["actionable"] = True
    return base
