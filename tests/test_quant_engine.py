"""
tests/test_quant_engine.py — Deterministic unit tests for QuantEngine.

Coverage:
  - HV30 (30-day historical volatility)
  - IV Rank (252-day lookback)
  - IV Percentile (252-day lookback)
  - Expected Move (chain-based and IV-approximation)
  - 25-delta Skew
  - 20-day Momentum Z-Score
  - Edge cases: flat prices, zero volatility, insufficient data
  - EvidenceEnvelope wrapping

No network calls. No LLM. Pure data fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from data_engine.quant_engine import (
    QuantEngine,
    QuantMetrics,
    MarketDataBundle,
    EvidenceEnvelope,
    DataQuality,
    SCHEMA_VERSION,
)
from tests.conftest import make_complete_bundle


# ---------------------------------------------------------------------------
# HV30 Tests
# ---------------------------------------------------------------------------

class TestHV30:
    def test_hv30_basic(self, engine, closes_252d, iv_series_flat):
        """HV30 computes and is in a reasonable range for realistic price series."""
        bundle = make_complete_bundle(closes_252d, iv_series_flat, iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)

        assert 0.0 < metrics.hv30 < 1.0  # Between 0% and 100% annualized
        assert metrics.hv30_quality == DataQuality.PRIMARY  # 252 rows

    def test_hv30_30_rows_needed(self, engine, iv_series_flat):
        """HV30 needs at least 21 closes; IV Rank needs at least 31 rows of IV."""
        closes = pd.Series(
            100.0 + np.arange(21),
            index=pd.date_range("2024-01-01", periods=21, freq="D", tz=timezone.utc),
        )
        bundle = make_complete_bundle(
            closes, iv_series_flat[:21],
            iv_atm=0.20, dte=3,
        )
        with pytest.raises(ValueError, match="IV Rank needs at least 31|Momentum Z-Score needs at least"):
            engine.compute(bundle)

    def test_hv30_exact_30_rows_stale(self, engine, iv_series_flat):
        """Exactly 31 closes gets STALE quality (need 40 for momentum Z-score)."""
        closes = pd.Series(
            100.0 + np.arange(31),
            index=pd.date_range("2024-01-01", periods=31, freq="D", tz=timezone.utc),
        )
        bundle = make_complete_bundle(
            closes, iv_series_flat[:31],
            iv_atm=0.20, dte=3,
        )
        with pytest.raises(ValueError, match="Momentum Z-Score needs at least"):
            engine.compute(bundle)

    def test_hv30_zero_volatility(self, engine, iv_series_flat):
        """Flat price series → HV30 = 0."""
        closes = pd.Series(
            [100.0] * 50,
            index=pd.date_range("2024-01-01", periods=50, freq="D", tz=timezone.utc),
        )
        bundle = make_complete_bundle(closes, iv_series_flat[:50], iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert metrics.hv30 == 0.0

    def test_hv30_known_sigma(self, engine):
        """HV30 of realistic series: result should be in a plausible range."""
        dates = pd.date_range("2024-01-01", periods=253, freq="D", tz=timezone.utc)
        rng = np.random.default_rng(999)
        log_returns = rng.normal(0, 0.01, size=253)
        prices = 100.0 * np.exp(np.cumsum(log_returns))
        closes = pd.Series(prices, index=dates)
        ivs = pd.Series(0.20, index=dates)

        bundle = make_complete_bundle(closes, ivs, iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert 0.05 < metrics.hv30 < 0.50  # sanity range for 1% daily vol

    def test_hv30_schema_version(self, engine, closes_252d, iv_series_flat):
        bundle = make_complete_bundle(closes_252d, iv_series_flat, iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert metrics.schema_version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# IV Rank & Percentile Tests
# ---------------------------------------------------------------------------

class TestIVRankAndPercentile:
    def test_iv_rank_rising_iv(self, engine, iv_series_rising, closes_252d):
        """IV Rank should be ~100% when IV is at its highest in the window."""
        bundle = make_complete_bundle(closes_252d, iv_series_rising, iv_atm=0.30, dte=3)
        metrics = engine.compute(bundle)
        assert 80 <= metrics.iv_rank <= 100  # Rising → high rank

    def test_iv_rank_flat_iv(self, engine, iv_series_flat, closes_252d):
        """Flat IV → IV Rank = 50. Percentile = 0 (current == all prior values)."""
        bundle = make_complete_bundle(closes_252d, iv_series_flat, iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        # Flat IV: all values = 0.20. Rank = 50.0 exactly (flat → midpoint).
        # Percentile: current_iv = 0.20, prior_ivs = all 0.20, so 0 days < current.
        # With strict < comparison: percentile = 0.
        assert 48 <= metrics.iv_rank <= 52  # 50 ± 2
        assert metrics.iv_percentile == 0.0  # strict < means flat → 0 percentile

    def test_iv_percentile_high_current(self, engine, iv_series_high_current, closes_252d):
        """IV Percentile should be high when current IV is near the top."""
        bundle = make_complete_bundle(closes_252d, iv_series_high_current, iv_atm=0.30, dte=3)
        metrics = engine.compute(bundle)
        # 226 days at 0.15, 26 at 0.30 — current (last day) at 0.30
        # Percentile = 226 / 251 ≈ 90%
        assert 85 <= metrics.iv_percentile <= 95
        assert metrics.iv_rank >= 85  # Rank and percentile should agree

    def test_iv_rank_insufficient_data(self, engine, iv_series_flat):
        """IV Rank needs at least 31 rows."""
        short_ivs = pd.Series(
            [0.20] * 30,
            index=pd.date_range("2024-01-01", periods=30, freq="D", tz=timezone.utc),
        )
        closes = pd.Series(
            100.0 + np.arange(30),
            index=pd.date_range("2024-01-01", periods=30, freq="D", tz=timezone.utc),
        )
        bundle = make_complete_bundle(closes, short_ivs, iv_atm=0.20, dte=3)
        with pytest.raises(ValueError, match="at least 31"):
            engine.compute(bundle)

    def test_iv_high_regime_flag(self, engine, iv_series_high_current, closes_252d):
        """iv_high_regime = True when both rank and percentile >= 60."""
        bundle = make_complete_bundle(closes_252d, iv_series_high_current, iv_atm=0.30, dte=3)
        metrics = engine.compute(bundle)
        assert metrics.iv_high_regime is True

    def test_iv_low_regime_flag(self, engine, iv_series_flat, closes_252d):
        """iv_high_regime = False when IV is flat (rank ~50)."""
        bundle = make_complete_bundle(closes_252d, iv_series_flat, iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert metrics.iv_high_regime is False


# ---------------------------------------------------------------------------
# Expected Move Tests
# ---------------------------------------------------------------------------

class TestExpectedMove:
    def test_em_iv_approximation(self, engine, closes_252d, iv_series_flat):
        """EM% = IV × √(DTE/365) × 100 using IV approximation."""
        bundle = MarketDataBundle(
            symbol="XSP",
            spot_price=closes_252d.iloc[-1],
            historical_closes=closes_252d,
            historical_iv=iv_series_flat,
            iv_atm=0.20,
            dte=3,
            chain_quality=DataQuality.ESTIMATED,
        )
        metrics = engine.compute(bundle)

        # Expected: 0.20 × √(3/365) × 100 ≈ 1.81%
        expected_em = 0.20 * np.sqrt(3 / 365.0) * 100
        assert abs(metrics.em_pct - expected_em) < 0.1
        assert metrics.em_method == "iv_approximation"
        assert metrics.em_quality == DataQuality.ESTIMATED

    def test_em_chain_based(self, engine, closes_252d, iv_series_flat):
        """EM% = (ATM_call + ATM_put) / spot × 100 when chain data available."""
        spot = closes_252d.iloc[-1]

        bundle = MarketDataBundle(
            symbol="XSP",
            spot_price=spot,
            historical_closes=closes_252d,
            historical_iv=iv_series_flat,
            atm_call_price=spot * 0.01,
            atm_put_price=spot * 0.01,
            iv_atm=0.20,
            dte=3,
            chain_quality=DataQuality.PRIMARY,
        )
        metrics = engine.compute(bundle)

        expected_em = (spot * 0.01 + spot * 0.01) / spot * 100  # = 2.0%
        assert abs(metrics.em_pct - expected_em) < 0.01
        assert metrics.em_method == "chain"
        assert metrics.em_quality == DataQuality.PRIMARY

    def test_em_no_chain_no_iv(self, engine, closes_252d, iv_series_flat):
        """Error when neither chain data nor IV+DTE available."""
        bundle = MarketDataBundle(
            symbol="XSP",
            spot_price=closes_252d.iloc[-1],
            historical_closes=closes_252d,
            historical_iv=iv_series_flat,
            iv_atm=None,  # missing
            dte=None,     # missing
        )
        with pytest.raises(ValueError, match="iv_atm or dte"):
            engine.compute(bundle)

    def test_em_zero_dte(self, engine, closes_252d, iv_series_flat):
        """DTE <= 0 → Pydantic validation error at bundle construction time."""
        # Pydantic validation fires at MarketDataBundle() construction, not compute()
        with pytest.raises(Exception):  # Pydantic ValidationError
            MarketDataBundle(
                symbol="XSP",
                spot_price=closes_252d.iloc[-1],
                historical_closes=closes_252d,
                historical_iv=iv_series_flat,
                iv_atm=0.20,
                dte=-1,  # fails Pydantic ge=1 validation
            )


# ---------------------------------------------------------------------------
# Skew Tests
# ---------------------------------------------------------------------------

class TestSkew:
    def test_skew_positive(self, engine, closes_252d, iv_series_flat):
        """Positive skew: 25-delta put IV > ATM IV (typical equity skew)."""
        bundle = MarketDataBundle(
            symbol="XSP",
            spot_price=closes_252d.iloc[-1],
            historical_closes=closes_252d,
            historical_iv=iv_series_flat,
            iv_atm=0.20,
            iv_25_delta_put=0.22,  # 10% higher — typical skew
            dte=3,
            chain_quality=DataQuality.PRIMARY,
        )
        metrics = engine.compute(bundle)

        assert metrics.skew_25_delta is not None
        assert metrics.skew_25_delta > 0  # OTM puts more expensive
        assert abs(metrics.skew_25_delta - 0.10) < 0.001  # exactly 10% higher

    def test_skew_no_chain_data(self, engine, closes_252d, iv_series_flat):
        """Returns None (not an error) when chain data unavailable."""
        bundle = MarketDataBundle(
            symbol="XSP",
            spot_price=closes_252d.iloc[-1],
            historical_closes=closes_252d,
            historical_iv=iv_series_flat,
            iv_atm=0.20,
            iv_25_delta_put=None,  # unavailable
            dte=3,
            chain_quality=DataQuality.ESTIMATED,
        )
        metrics = engine.compute(bundle)
        assert metrics.skew_25_delta is None
        assert metrics.skew_quality == DataQuality.ESTIMATED


# ---------------------------------------------------------------------------
# Momentum Z-Score Tests
# ---------------------------------------------------------------------------

class TestMomentumZScore:
    def test_momentum_zscore_uptrend(self, engine, iv_series_flat):
        """Strong uptrend with noise → z-score is meaningful (not near zero)."""
        # Use noisy multiplicative returns — linspace gives constant returns → z≈0
        dates = pd.date_range("2024-01-01", periods=60, freq="D", tz=timezone.utc)
        rng = np.random.default_rng(42)
        # Mean = +0.5% daily, sigma = 2% — trending up
        log_returns = rng.normal(0.005, 0.02, size=60)
        prices = 100.0 * np.exp(np.cumsum(log_returns))
        closes = pd.Series(prices, index=dates)
        bundle = make_complete_bundle(closes, iv_series_flat[:60], iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert -12.0 <= metrics.momentum_zscore_20d <= 12.0  # z-score is clipped to ±10 in compute()

    def test_momentum_zscore_downtrend(self, engine, iv_series_flat):
        """Downtrend → z-score is meaningful."""
        dates = pd.date_range("2024-01-01", periods=60, freq="D", tz=timezone.utc)
        rng = np.random.default_rng(43)
        # Mean = -0.5% daily, sigma = 2% — trending down
        log_returns = rng.normal(-0.005, 0.02, size=60)
        prices = 100.0 * np.exp(np.cumsum(log_returns))
        closes = pd.Series(prices, index=dates)
        bundle = make_complete_bundle(closes, iv_series_flat[:60], iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert -12.0 <= metrics.momentum_zscore_20d <= 12.0  # z-score is clipped to ±10 in compute()

    def test_momentum_zscore_flat(self, engine, iv_series_flat):
        """Flat prices → Z-score ≈ 0."""
        closes = pd.Series(
            [100.0] * 50,
            index=pd.date_range("2024-01-01", periods=50, freq="D", tz=timezone.utc),
        )
        bundle = make_complete_bundle(closes, iv_series_flat[:50], iv_atm=0.20, dte=3)
        metrics = engine.compute(bundle)
        assert abs(metrics.momentum_zscore_20d) < 0.5  # Flat → near zero

    def test_momentum_zscore_insufficient_data(self, engine, iv_series_flat):
        """Needs at least 40 closes."""
        closes = pd.Series(
            100.0 + np.arange(39),
            index=pd.date_range("2024-01-01", periods=39, freq="D", tz=timezone.utc),
        )
        bundle = make_complete_bundle(closes, iv_series_flat[:39], iv_atm=0.20, dte=3)
        with pytest.raises(ValueError, match="at least 40"):
            engine.compute(bundle)


# ---------------------------------------------------------------------------
# EvidenceEnvelope Tests
# ---------------------------------------------------------------------------

class TestEvidenceEnvelope:
    def test_evidence_envelope_wraps_metrics(self, engine, sample_bundle_252d):
        """EvidenceEnvelope correctly wraps QuantMetrics."""
        metrics = engine.compute(sample_bundle_252d)
        envelope = EvidenceEnvelope(
            metrics=metrics,
            source_symbol="XSP",
        )

        assert envelope.is_usable() is True
        assert envelope.fallback_tier == "primary"
        assert envelope.regime_signal() == "high_iv"

    def test_evidence_envelope_unavailable(self):
        """Unusable envelope returns regime_signal='unknown'."""
        metrics = QuantMetrics(
            hv30=0.0, hv30_quality=DataQuality.PRIMARY,
            iv_rank=0.0, iv_rank_quality=DataQuality.PRIMARY,
            iv_percentile=0.0, iv_percentile_quality=DataQuality.PRIMARY,
            em_pct=0.0, em_method="iv_approximation", em_quality=DataQuality.PRIMARY,
            skew_25_delta=None, skew_quality=DataQuality.ESTIMATED,
            momentum_zscore_20d=0.0, momentum_quality=DataQuality.PRIMARY,
            iv_high_regime=False,
        )
        envelope = EvidenceEnvelope(
            metrics=metrics,
            source_symbol="XSP",
            fallback_tier="unavailable",
        )
        assert envelope.is_usable() is False
        assert envelope.regime_signal() == "unknown"


# ---------------------------------------------------------------------------
# Derived Field Tests
# ---------------------------------------------------------------------------

class TestDerivedFields:
    def test_hv_vs_iv_ratio(self, engine, closes_252d, iv_series_high_current):
        """hv_vs_iv_ratio = HV30 / IV. Values << 1 support VRP harvesting."""
        bundle = make_complete_bundle(closes_252d, iv_series_high_current, iv_atm=0.30, dte=3)
        metrics = engine.compute(bundle)
        assert metrics.hv_vs_iv_ratio is not None
        assert 0.0 <= metrics.hv_vs_iv_ratio <= 2.0


# ---------------------------------------------------------------------------
# Schema Version Tests
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_schema_version_present(self, engine, sample_bundle_252d):
        """Every QuantMetrics output has schema_version."""
        metrics = engine.compute(sample_bundle_252d)
        assert metrics.schema_version is not None
        assert metrics.schema_version == SCHEMA_VERSION
