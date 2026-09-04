from quant_engine.entry_confidence import build_entry_confidence


def test_entry_confidence_uses_real_underlying_history_as_explicit_proxy():
    result = build_entry_confidence({
        "state": "ENTER_CONFIRMED",
        "direction": "BULLISH",
        "actionable": True,
        "setup_probability": 0.72,
        "setup_lower_bound": 0.64,
        "setup_successes": 80,
        "setup_sample_size": 120,
        "volatility_regime": "LOW",
        "horizon": 1,
    })

    assert result["state"] == "GREEN_PROXY"
    assert result["actionable"] is True
    assert result["source"] == "stock_bars"
    assert result["warning"] == "not_option_pnl_calibrated"


def test_entry_confidence_proxy_does_not_wait_for_strict_calibration_lower_bound():
    result = build_entry_confidence({
        "state": "WAIT_SEE",
        "direction": "BULLISH",
        "actionable": False,
        "setup_probability": 0.55,
        "setup_lower_bound": 0.42,
        "setup_successes": 44,
        "setup_sample_size": 80,
        "horizon_alignment": {"passed": True, "agreeing": 2, "total": 3},
        "volatility_regime": "LOW",
        "horizon": 1,
    })

    assert result["state"] == "GREEN_PROXY"
    assert result["actionable"] is True
    assert result["probability"] == 0.55
    assert result["lower_bound"] == 0.42


def test_entry_confidence_requires_historical_samples_and_alignment_for_proxy():
    result = build_entry_confidence({
        "state": "WAIT_SEE",
        "direction": "BULLISH",
        "setup_probability": 0.70,
        "setup_sample_size": 29,
        "horizon_alignment": {"passed": False, "agreeing": 1, "total": 3},
    })

    assert result["state"] == "WAIT_SEE"
    assert result["actionable"] is False
    assert "insufficient_proxy_samples" in result["reasons"]
    assert "proxy_horizon_alignment_below_threshold" in result["reasons"]
