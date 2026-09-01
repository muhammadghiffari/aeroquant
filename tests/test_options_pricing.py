"""
Tests for data_engine/options_pricing.py — deterministic options pricing utilities.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest
from data_engine.options_pricing import (
    bs_put_delta,
    bs_call_delta,
    put_strike_from_delta,
    call_strike_from_delta,
    bs_put_price,
    bs_call_price,
    nearest_valid_expiry,
    _norm_cdf,
    _norm_ppf,
    XSP_STRIKE_INCREMENT,
    SHORT_PUT_DELTA_TARGET,
    SHORT_CALL_DELTA_TARGET,
)


class TestNormalCDF:
    def test_cdf_at_zero(self):
        assert abs(_norm_cdf(0.0) - 0.5) < 1e-6

    def test_cdf_symmetry(self):
        x = 1.5
        assert abs(_norm_cdf(x) + _norm_cdf(-x) - 1.0) < 1e-6

    def test_cdf_bounds(self):
        for x in [-10, -5, -1, 0, 1, 5, 10]:
            c = _norm_cdf(x)
            assert 0.0 <= c <= 1.0

    def test_cdf_known_values(self):
        # 1.96 ≈ 97.5th percentile
        assert abs(_norm_cdf(1.96) - 0.975) < 0.001
        # 2.576 ≈ 99.5th percentile
        assert abs(_norm_cdf(2.576) - 0.995) < 0.001


class TestNormalPPF:
    def test_ppf_round_trip(self):
        for p in [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]:
            z = _norm_ppf(p)
            assert abs(_norm_cdf(z) - p) < 1e-6

    def test_ppf_symmetry(self):
        p = 0.3
        assert abs(_norm_ppf(p) + _norm_ppf(1 - p)) < 1e-6

    def test_ppf_center(self):
        assert abs(_norm_ppf(0.5)) < 1e-9


class TestBlackScholesDelta:
    def test_put_delta_is_negative_for_otm_put(self):
        delta = bs_put_delta(spot=100.0, strike=95.0, iv=0.20, dte=30)
        assert -1.0 < delta < 0.0

    def test_put_delta_becomes_more_negative_as_put_becomes_more_itm(self):
        far_otm = bs_put_delta(spot=100.0, strike=90.0, iv=0.20, dte=30)
        near_atm = bs_put_delta(spot=100.0, strike=100.0, iv=0.20, dte=30)
        deep_itm = bs_put_delta(spot=100.0, strike=110.0, iv=0.20, dte=30)
        assert -1.0 < deep_itm < near_atm < far_otm < 0.0

    def test_call_delta_positive_for_otm(self):
        # OTM call (strike > spot) should have small positive delta
        delta = bs_call_delta(spot=100.0, strike=105.0, iv=0.20, dte=30)
        assert 0.0 < delta < 1.0

    def test_call_delta_near_one_for_deep_itm(self):
        # Deep ITM call should have delta near 1
        delta = bs_call_delta(spot=100.0, strike=80.0, iv=0.20, dte=30)
        assert 0.9 < delta <= 1.0

    def test_delta_zero_at_expiry(self):
        assert bs_put_delta(spot=100.0, strike=105.0, iv=0.20, dte=0) == 0.0
        assert bs_call_delta(spot=100.0, strike=105.0, iv=0.20, dte=0) == 0.0

    def test_delta_atm_approx_neg_half(self):
        # ATM put at short expiry: delta ≈ -0.5
        delta = bs_put_delta(spot=100.0, strike=100.0, iv=0.20, dte=5)
        assert -0.7 < delta < -0.3

    def test_delta_short_dte_more_sensitive(self):
        # Short-dte ATM delta closer to -0.5 than long-dte
        short = bs_put_delta(spot=100.0, strike=100.0, iv=0.20, dte=3)
        long = bs_put_delta(spot=100.0, strike=100.0, iv=0.20, dte=60)
        assert abs(short - (-0.5)) < abs(long - (-0.5))

    def test_delta_zero_on_invalid_input(self):
        assert bs_put_delta(spot=0.0, strike=100.0, iv=0.20, dte=30) == 0.0
        assert bs_put_delta(spot=100.0, strike=100.0, iv=0.0, dte=30) == 0.0
        assert bs_call_delta(spot=0.0, strike=100.0, iv=0.20, dte=30) == 0.0


class TestDeltaToStrike:
    def test_put_strike_below_spot(self):
        # 16-delta put should be below spot
        spot = 570.0
        strike = put_strike_from_delta(spot, iv=0.18, dte=3, target_delta=-0.16)
        assert strike < spot
        assert strike > 0

    def test_call_strike_above_spot(self):
        # 16-delta call should be above spot
        spot = 570.0
        strike = call_strike_from_delta(spot, iv=0.18, dte=3, target_delta=0.16)
        assert strike > spot
        assert strike > 0

    def test_put_strike_delta_approximation(self):
        # Verify the strike produces a delta close to target
        spot = 570.0
        iv = 0.18
        dte = 3
        strike = put_strike_from_delta(spot, iv, dte, target_delta=-0.16)
        actual_delta = bs_put_delta(spot, strike, iv, dte)
        # Should be within ±0.05 of target
        assert abs(actual_delta - (-0.16)) < 0.05

    def test_call_strike_delta_approximation(self):
        spot = 570.0
        iv = 0.18
        dte = 3
        strike = call_strike_from_delta(spot, iv, dte, target_delta=0.16)
        actual_delta = bs_call_delta(spot, strike, iv, dte)
        assert abs(actual_delta - 0.16) < 0.05

    def test_strike_rounds_to_increment(self):
        spot = 570.0
        for dte in [1, 3, 5]:
            strike = put_strike_from_delta(spot, 0.18, dte, -0.16)
            assert abs(strike % XSP_STRIKE_INCREMENT) < 1e-9

    def test_higher_iv_gives_further_otm_strike(self):
        spot = 570.0
        dte = 3
        low_iv_strike = put_strike_from_delta(spot, 0.15, dte, -0.16)
        high_iv_strike = put_strike_from_delta(spot, 0.25, dte, -0.16)
        assert high_iv_strike < low_iv_strike  # higher IV = further OTM for same delta

    def test_shorter_dte_gives_strike_closer_to_spot_at_fixed_delta(self):
        spot = 570.0
        iv = 0.18
        short_dte_strike = put_strike_from_delta(spot, iv, 1, -0.16)
        long_dte_strike = put_strike_from_delta(spot, iv, 5, -0.16)
        assert short_dte_strike > long_dte_strike

    def test_25_delta_put_is_closer_to_spot_than_16_delta_put(self):
        spot = 570.0
        strike_16 = put_strike_from_delta(spot, 0.18, 3, -0.16)
        strike_25 = put_strike_from_delta(spot, 0.18, 3, -0.25)
        assert strike_25 > strike_16

    def test_rounded_put_strike_remains_near_target_delta(self):
        spot = 570.0
        strike = put_strike_from_delta(spot, 0.18, 3, -0.16)
        assert strike < spot
        assert abs(bs_put_delta(spot, strike, 0.18, 3) - (-0.16)) < 0.05


class TestBlackScholesPrice:
    def test_put_price_positive_for_otm(self):
        # OTM put (strike > spot) should have positive price
        price = bs_put_price(spot=100.0, strike=105.0, iv=0.20, dte=30)
        assert price > 0

    def test_put_price_follows_intrinsic_value_at_expiry(self):
        assert bs_put_price(spot=100.0, strike=95.0, iv=0.20, dte=0) == 0.0
        assert bs_put_price(spot=100.0, strike=105.0, iv=0.20, dte=0) == 5.0
        assert bs_call_price(spot=100.0, strike=95.0, iv=0.20, dte=0) == 5.0

    def test_call_price_intrinsic_lower_bound(self):
        # Call price >= max(0, S - K * exp(-rT))
        spot = 100.0
        strike = 105.0
        price = bs_call_price(spot, strike, 0.20, 30)
        intrinsic = max(0.0, spot - strike * math.exp(-0.05 * 30 / 365))
        assert price >= intrinsic - 0.01

    def test_put_exceeds_call_for_strike_above_spot(self):
        call = bs_call_price(spot=100.0, strike=105.0, iv=0.20, dte=30)
        put = bs_put_price(spot=100.0, strike=105.0, iv=0.20, dte=30)
        assert put > call

    def test_put_call_parity(self):
        # C - P = S - K * exp(-rT)
        spot = 100.0
        strike = 105.0
        iv = 0.20
        dte = 30
        r = 0.05
        call = bs_call_price(spot, strike, iv, dte, r)
        put = bs_put_price(spot, strike, iv, dte, r)
        parity = call - put
        expected = spot - strike * math.exp(-r * dte / 365)
        assert abs(parity - expected) < 0.01

    def test_price_zero_on_invalid_input(self):
        assert bs_put_price(spot=0.0, strike=100.0, iv=0.20, dte=30) == 0.0
        assert bs_call_price(spot=0.0, strike=100.0, iv=0.20, dte=30) == 0.0


class TestNearestValidExpiry:
    AS_OF = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)

    def test_prefers_closest_to_preferred_dte_deterministically(self):
        expirations = ["2026-09-04", "2026-09-05", "2026-09-08"]
        assert nearest_valid_expiry(
            expirations, min_dte=1, max_dte=10, preferred_dte=3, as_of=self.AS_OF
        ) == "2026-09-04"

    def test_rejects_expired_and_out_of_range_expiries(self):
        result = nearest_valid_expiry(
            ["2026-08-31", "2026-10-01"],
            min_dte=1,
            max_dte=5,
            preferred_dte=3,
            as_of=self.AS_OF,
        )
        assert result is None

    def test_requires_explicit_as_of(self):
        assert nearest_valid_expiry(["2026-09-04"]) is None

    def test_rejects_naive_datetime_reference(self):
        assert nearest_valid_expiry(["2026-09-04"], as_of=datetime(2026, 9, 1)) is None

    def test_empty_list_returns_none(self):
        assert nearest_valid_expiry([], as_of=self.AS_OF) is None


class TestAnalyticalStability:
    @pytest.mark.parametrize("probability", [1e-12, 1e-9, 1e-6, 1 - 1e-6, 1 - 1e-9, 1 - 1e-12])
    def test_normal_round_trip_is_stable_in_representative_tails(self, probability):
        z_score = _norm_ppf(probability)
        assert math.isfinite(z_score)
        assert abs(_norm_cdf(z_score) - probability) <= max(2e-15, probability * 5e-6)

    def test_prices_are_monotonic_in_strike(self):
        low_strike_call = bs_call_price(100.0, 95.0, 0.20, 30)
        high_strike_call = bs_call_price(100.0, 105.0, 0.20, 30)
        low_strike_put = bs_put_price(100.0, 95.0, 0.20, 30)
        high_strike_put = bs_put_price(100.0, 105.0, 0.20, 30)
        assert low_strike_call > high_strike_call
        assert high_strike_put > low_strike_put
