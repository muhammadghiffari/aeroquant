"""Explicit entry confidence sourced from historical underlying bars."""
from __future__ import annotations

import config

CONFIDENCE_VERSION = "underlying-history-1h-v1"


def build_entry_confidence(underlying_confidence: dict) -> dict:
    """Expose stock-history confidence without mislabeling it as option P/L."""
    source = dict(underlying_confidence or {})
    direction = str(source.get("direction", "WAIT")).upper()
    probability = float(source.get("setup_probability") or 0.0)
    sample_size = int(source.get("setup_sample_size") or 0)
    alignment = source.get("horizon_alignment") or {}
    proxy_reasons = []
    if direction not in {"BULLISH", "BEARISH"}:
        proxy_reasons.append("proxy_direction_unavailable")
    if sample_size < config.MOMENTUM_MIN_SAMPLES:
        proxy_reasons.append("insufficient_proxy_samples")
    if probability < config.MOMENTUM_PROXY_MIN_PROBABILITY:
        proxy_reasons.append("proxy_probability_below_threshold")
    if alignment and not alignment.get("passed", False):
        proxy_reasons.append("proxy_horizon_alignment_below_threshold")
    actionable = not proxy_reasons
    state = "GREEN_PROXY" if actionable else source.get("state", "WAIT_DATA")
    return {
        "confidence_version": CONFIDENCE_VERSION,
        "historical_confidence_advisory": True,
        "source": "stock_bars",
        "timeframe": "1H",
        "state": state,
        "actionable": actionable,
        "direction": direction,
        "volatility_regime": source.get("volatility_regime"),
        "probability": probability,
        "lower_bound": source.get("setup_lower_bound", 0.0),
        "successes": source.get("setup_successes", 0),
        "sample_size": sample_size,
        "horizon": source.get("horizon", 1),
        "proxy_probability_floor": config.MOMENTUM_PROXY_MIN_PROBABILITY,
        "warning": "not_option_pnl_calibrated",
        "reasons": proxy_reasons,
    }
