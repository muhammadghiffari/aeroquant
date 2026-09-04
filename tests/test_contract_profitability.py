from datetime import datetime, timezone

import pytest

from data_engine.option_data import OptionContract
from quant_engine.contract_profitability import evaluate_long_option


def _contract(**overrides):
    values = {
        "symbol": "AAPL260919C00200000",
        "underlying": "AAPL",
        "expiry": "2026-09-19",
        "opt_type": "call",
        "strike": 200.0,
        "bid": 2.0,
        "ask": 2.1,
        "mid": 2.05,
        "spread_pct": 0.0488,
        "iv": 0.25,
        "delta": 0.55,
        "theta": -0.10,
        "dte": 14,
        "quote_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    values.update(overrides)
    return OptionContract(**values)


def test_profitability_uses_entry_ask_exit_bid_and_theta_decay():
    result = evaluate_long_option(
        _contract(),
        spot=200.0,
        expected_move_abs=5.0,
        underlying_probability=0.70,
        direction="BULLISH",
        horizon_bars=5,
    )

    assert result["entry_price"] == pytest.approx(2.10)
    assert result["projected_exit_bid"] == pytest.approx(4.25)
    assert result["target_net_pnl_usd"] == pytest.approx(215.0)
    assert result["expected_pnl_usd"] > 0
    assert result["breakeven_move_abs"] > 0


def test_profitability_defaults_to_one_hour_horizon():
    result = evaluate_long_option(
        _contract(),
        spot=200.0,
        expected_move_abs=5.0,
        underlying_probability=0.70,
        direction="BULLISH",
    )

    assert result["horizon_bars"] == 1


def test_profitability_fails_closed_for_wrong_direction_or_stale_quote():
    wrong_side = evaluate_long_option(
        _contract(opt_type="put"),
        spot=200.0,
        expected_move_abs=5.0,
        underlying_probability=0.70,
        direction="BULLISH",
    )
    stale = evaluate_long_option(
        _contract(quote_timestamp="2020-01-01T00:00:00+00:00"),
        spot=200.0,
        expected_move_abs=5.0,
        underlying_probability=0.70,
        direction="BULLISH",
    )

    assert wrong_side["valid"] is False
    assert "contract_direction_mismatch" in wrong_side["reasons"]
    assert stale["valid"] is False
    assert "stale_quote" in stale["reasons"]


def test_profitability_fails_closed_when_quote_timestamp_is_missing():
    result = evaluate_long_option(
        _contract(quote_timestamp=None),
        spot=200.0,
        expected_move_abs=5.0,
        underlying_probability=0.70,
        direction="BULLISH",
    )

    assert result["valid"] is False
    assert "missing_quote_timestamp" in result["reasons"]
