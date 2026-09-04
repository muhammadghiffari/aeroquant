"""Empirical confidence for contract-level net profitability outcomes."""
from __future__ import annotations

import config
from quant_engine.momentum import _wilson_lower_bound


CONFIDENCE_VERSION = "option-profit-calibrated-v1"


def dte_bucket(dte: int) -> str:
    return "7_14" if int(dte) <= 14 else "15_21"


def delta_bucket(delta: float) -> str:
    return "45_55" if abs(float(delta)) <= 0.55 else "55_70"


def build_contract_confidence(
    outcomes: list[dict],
    *,
    direction: str,
    volatility_regime: str | None,
    dte: int,
    delta: float,
    timeframe: str = "1H",
    min_samples: int | None = None,
    threshold: float | None = None,
) -> dict:
    """Return a fail-closed lower bound for matched contract outcomes."""
    direction = str(direction).upper()
    timeframe = str(timeframe).upper()
    expected = {
        "timeframe": timeframe,
        "direction": direction,
        "volatility_regime": volatility_regime,
        "dte_bucket": dte_bucket(dte),
        "delta_bucket": delta_bucket(delta),
    }
    matched = []
    for outcome in outcomes or []:
        if outcome.get("profitable") not in {True, False}:
            continue
        if any(
            outcome.get(key) is not None and outcome.get(key) != value
            for key, value in expected.items()
        ):
            continue
        matched.append(outcome)

    sample_size = len(matched)
    successes = sum(bool(item["profitable"]) for item in matched)
    probability = successes / sample_size if sample_size else 0.0
    lower_bound = _wilson_lower_bound(successes, sample_size) if sample_size else 0.0
    min_samples = config.CONTRACT_CONFIDENCE_MIN_SAMPLES if min_samples is None else int(min_samples)
    threshold = config.MOMENTUM_MIN_PROBABILITY_LB if threshold is None else float(threshold)
    reasons = []
    if sample_size == 0:
        state = "WAIT_DATA"
        reasons.append("no_matched_contract_outcomes")
    else:
        if sample_size < min_samples:
            reasons.append("insufficient_contract_samples")
        if lower_bound < threshold:
            reasons.append("contract_lower_bound_below_threshold")
        state = "GREEN" if not reasons else "AMBER"
    return {
        "confidence_version": CONFIDENCE_VERSION,
        "timeframe": timeframe,
        "state": state,
        "actionable": state == "GREEN",
        "direction": direction,
        "volatility_regime": volatility_regime,
        "dte_bucket": expected["dte_bucket"],
        "delta_bucket": expected["delta_bucket"],
        "probability": round(probability, 4),
        "lower_bound": round(lower_bound, 4),
        "successes": successes,
        "sample_size": sample_size,
        "min_samples": min_samples,
        "threshold": threshold,
        "reasons": reasons,
    }
