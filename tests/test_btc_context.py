from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from quant_engine.btc_context import apply_btc_context, build_btc_context


def _bars(direction: str, shock: bool = False, count: int = 360) -> pd.DataFrame:
    index = pd.date_range(
        datetime.now(timezone.utc) - timedelta(minutes=count),
        periods=count,
        freq="min",
        tz="UTC",
    )
    if direction == "bullish":
        close = np.linspace(100, 105, count)
    else:
        close = np.linspace(105, 100, count)
    if shock:
        close[-16:] *= 1.04 if direction == "bullish" else 0.96
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(count, 1000.0),
        },
        index=index,
    )


def test_btc_context_classifies_recent_bullish_regime():
    context = build_btc_context(_bars("bullish"))

    assert context["direction"] == "BULLISH"
    assert context["regime"] in {"BULLISH", "EXTREME_BULLISH"}
    assert context["data_quality"] == "fresh"


def test_btc_context_is_secondary_when_not_extreme():
    signal = {
        "direction": "BULLISH",
        "strategy_type": "LONG_CALL",
        "actionable": True,
        "confidence": 0.7,
    }
    context = {
        "direction": "BEARISH",
        "regime": "BEARISH",
        "data_quality": "fresh",
    }

    result = apply_btc_context(signal, context)

    assert result["direction"] == "BULLISH"
    assert result["strategy_type"] == "LONG_CALL"
    assert result["btc_alignment"] == "conflicting"
    assert result["confidence"] < signal["confidence"]
    assert result["sizing_multiplier"] < 1.0


def test_extreme_btc_regime_is_secondary_context_not_direction_override():
    signal = {
        "direction": "BULLISH",
        "strategy_type": "LONG_CALL",
        "actionable": True,
        "confidence": 0.7,
    }
    context = {
        "direction": "BEARISH",
        "regime": "EXTREME_BEARISH",
        "data_quality": "fresh",
    }

    result = apply_btc_context(signal, context)

    assert result["direction"] == "BULLISH"
    assert result["strategy_type"] == "LONG_CALL"
    assert result["btc_override"] is False
    assert result["btc_extreme_context"] is True
    assert result["sizing_multiplier"] < 1.0


def test_stale_btc_context_does_not_change_signal():
    signal = {
        "direction": "BULLISH",
        "strategy_type": "LONG_CALL",
        "actionable": True,
        "confidence": 0.7,
    }
    context = {
        "direction": "BEARISH",
        "regime": "EXTREME_BEARISH",
        "data_quality": "stale",
    }

    result = apply_btc_context(signal, context)

    assert result["direction"] == "BULLISH"
    assert result["btc_override"] is False
    assert "btc_data_stale" in result["reasons"]


def test_insufficient_btc_context_is_not_reported_as_stale():
    signal = {
        "direction": "BULLISH",
        "strategy_type": "LONG_CALL",
        "actionable": True,
        "confidence": 0.7,
    }

    result = apply_btc_context(
        signal,
        {"direction": "NEUTRAL", "regime": "NEUTRAL", "data_quality": "insufficient_bars"},
    )

    assert "btc_data_unavailable" in result["reasons"]
    assert "btc_data_stale" not in result["reasons"]
