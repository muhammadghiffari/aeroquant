"""Golden-value unit tests for the Quant Engine (pure math, no network)."""
import math

import pytest

from data_engine.option_data import OptionContract, is_entry_expiry_allowed, resolve_leg
from quant_engine.expected_move import expected_move
from quant_engine.probability import pop_proxy, skew_metrics
from quant_engine.trend_score import trend_metrics
from quant_engine.volatility_metrics import (
    hv_series,
    percentile_rank,
    realized_vol,
    volatility_metrics,
)


class FakeContract:
    def __init__(self, mid):
        self.mid = mid
        self.symbol = "X"
        self.expiry = "2026-09-25"
        self.dte = 30


def test_realized_vol_flat_series():
    closes = [100.0] * 40
    assert realized_vol(closes, 30) == 0.0


def test_realized_vol_constant_returns_is_zero():
    # Constant log-returns are pure drift => zero volatility.
    closes = [100.0 * math.exp(0.001 * i) for i in range(31)]
    assert realized_vol(closes, 30) == pytest.approx(0.0, abs=1e-12)


def test_realized_vol_matches_stdev_formula():
    import random
    import statistics

    random.seed(7)
    rets = [random.gauss(0, 0.01) for _ in range(30)]
    closes = [100.0]
    for r in rets:
        closes.append(closes[-1] * math.exp(r))
    hv = realized_vol(closes, 30)
    expected = statistics.stdev(rets) * math.sqrt(252)
    assert hv == pytest.approx(expected, rel=1e-9)


def test_hv_series_length_and_monotone_window():
    closes = list(range(100, 160))
    s = hv_series(closes, 20)
    assert len(s) == len(closes) - 1 - 20 + 1
    assert (s == s).all()


def test_percentile_rank_bounds_and_nan_guard():
    series = list(range(100))
    assert percentile_rank(50, series) == 50.0
    assert percentile_rank(-5, series) == 0.0
    assert percentile_rank(500, series) == 100.0
    assert percentile_rank(float("nan"), series) is None
    assert percentile_rank(50, [1.0, 2.0]) is None  # too short


def test_volatility_metrics_spread_sign():
    closes = [100 * (1 + 0.01 * ((-1) ** i)) for i in range(60)]
    m = volatility_metrics(closes, current_atm_iv=0.35)
    assert m["hv_iv_spread"] > 0
    assert m["iv_rank_method"] == "hv_proxy"


def test_expected_move():
    em = expected_move(200.0, FakeContract(3.0), FakeContract(2.0))
    assert em["atm_straddle_price"] == 5.0
    assert em["expected_move_pct"] == 2.5
    assert em["expected_move_abs"] == 5.0


def test_skew_metrics():
    sm = skew_metrics(
        {
            "put_25delta": {"iv": 0.28},
            "call_25delta": {"iv": 0.22},
        }
    )
    assert sm["skew_put_call_25delta"] == pytest.approx(0.06)


def test_pop_proxy():
    assert pop_proxy("BULL_PUT_SPREAD", short_leg_delta=-0.30) == pytest.approx(0.70)
    assert pop_proxy("LONG_CALL", long_leg_delta=0.45) == pytest.approx(0.45)
    assert pop_proxy("LONG_CALL") is None


def test_trend_metrics_uptrend_z_positive():
    closes = [100 + i for i in range(60)]
    t = trend_metrics(closes)
    assert t["z_score_20d"] > 0
    assert t["momentum_20d_pct"] > 0
    assert t["sma_20"] > t["sma_50"]


def test_resolve_leg_rejects_missing_strike_instead_of_snapping():
    chain = [
        OptionContract(
            symbol="SPY260902P00500000", underlying="SPY", expiry="2026-09-02",
            opt_type="put", strike=500.0, bid=1.0, ask=1.1, mid=1.05,
            spread_pct=0.095, iv=0.2, delta=-0.25, dte=7,
        )
    ]

    assert resolve_leg(chain, "put", 505.0, "2026-09-02") is None


def test_entry_expiry_must_survive_final_close_date():
    assert is_entry_expiry_allowed("2026-09-03") is False
    assert is_entry_expiry_allowed("2026-09-04") is False
    assert is_entry_expiry_allowed("2026-09-11") is True


def test_option_contract_preserves_quote_timestamp_for_freshness_checks():
    contract = OptionContract(
        symbol="SPY260911C00500000", underlying="SPY", expiry="2026-09-11",
        opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
        spread_pct=0.0488, iv=0.2, delta=0.55, dte=10,
        quote_timestamp="2026-09-01T13:30:00+00:00",
    )

    assert contract.as_dict()["quote_timestamp"] == "2026-09-01T13:30:00+00:00"
