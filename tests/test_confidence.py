import numpy as np
import pandas as pd

import config
from quant_engine.confidence import build_confidence_signal, confirm_horizons


def test_confidence_horizons_use_one_completed_hour():
    assert config.MOMENTUM_HORIZON == 1
    assert config.CONTRACT_CONFIDENCE_HORIZON == 1


def test_confidence_report_exposes_one_hour_forward_horizon():
    result = build_confidence_signal(_bars(np.geomspace(100.0, 150.0, 100)))

    assert result["horizon"] == 1


def _bars(closes):
    return pd.DataFrame({"close": closes, "volume": [1000] * len(closes)})


def test_confidence_confirms_a_repeated_bullish_setup():
    result = build_confidence_signal(_bars(np.geomspace(100.0, 300.0, 220)))

    assert result["state"] == "ENTER_CONFIRMED"
    assert result["direction"] == "BULLISH"
    assert result["setup_lower_bound"] >= 0.60
    assert result["setup_sample_size"] >= 30
    assert result["horizon_alignment"]["passed"] is True
    assert result["confidence_score"] <= result["setup_lower_bound"]


def test_confidence_waits_when_conditioned_sample_is_too_small():
    closes = np.r_[np.full(150, 100.0), np.geomspace(100.0, 120.0, 25)]
    result = build_confidence_signal(_bars(closes))

    assert result["state"] == "WAIT_SEE"
    assert result["setup_sample_size"] < 30


def test_confidence_requires_two_of_three_horizons():
    closes = np.r_[np.linspace(100.0, 90.0, 41), np.linspace(90.1, 95.0, 20)]

    result = confirm_horizons(closes, "BULLISH")

    assert result["agreeing"] == 2
    assert result["passed"] is True
    assert result["horizons"]["60"] == "BEARISH"


def test_confidence_returns_wait_data_for_invalid_closes():
    result = build_confidence_signal(_bars([100.0, np.nan, 101.0] * 30))

    assert result["state"] == "WAIT_DATA"
    assert result["actionable"] is False


def test_confidence_at_endpoint_ignores_future_bars():
    closes = np.geomspace(100.0, 180.0, 190)
    endpoint = 130
    original = build_confidence_signal(_bars(closes), as_of_index=endpoint)

    changed = closes.copy()
    changed[endpoint + 1 :] *= 4.0
    future_changed = build_confidence_signal(_bars(changed), as_of_index=endpoint)

    assert future_changed == original


def test_confidence_uses_configured_lower_bound_threshold(monkeypatch):
    assert config.MOMENTUM_MIN_PROBABILITY_LB == 0.60
    monkeypatch.setattr(config, "MOMENTUM_MIN_PROBABILITY_LB", 1.0)

    result = build_confidence_signal(_bars(np.geomspace(100.0, 300.0, 220)))

    assert result["state"] == "WAIT_SEE"
    assert "setup_lower_bound_below_threshold" in result["reasons"]
