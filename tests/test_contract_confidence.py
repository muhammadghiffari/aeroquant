from quant_engine.contract_confidence import build_contract_confidence


def _outcomes(successes, total):
    return [{"profitable": index < successes} for index in range(total)]


def test_contract_confidence_requires_thirty_matched_outcomes():
    result = build_contract_confidence(
        _outcomes(30, 30),
        direction="BULLISH",
        volatility_regime="LOW",
        dte=10,
        delta=0.55,
        min_samples=30,
        threshold=0.60,
    )

    assert result["state"] == "GREEN"
    assert result["lower_bound"] >= 0.60
    assert result["sample_size"] == 30


def test_contract_confidence_is_amber_when_evidence_is_insufficient():
    result = build_contract_confidence(
        _outcomes(20, 29),
        direction="BULLISH",
        volatility_regime="HIGH",
        dte=18,
        delta=0.65,
        min_samples=30,
        threshold=0.60,
    )

    assert result["state"] == "AMBER"
    assert result["actionable"] is False
    assert "insufficient_contract_samples" in result["reasons"]


def test_contract_confidence_matches_only_the_requested_timeframe():
    outcomes = [
        {"profitable": True, "timeframe": "1H"},
        {"profitable": False, "timeframe": "1D"},
    ]

    result = build_contract_confidence(
        outcomes,
        direction="BULLISH",
        volatility_regime="LOW",
        dte=10,
        delta=0.55,
        timeframe="1H",
        min_samples=1,
        threshold=0.50,
    )

    assert result["timeframe"] == "1H"
    assert result["sample_size"] == 1
    assert result["successes"] == 1
