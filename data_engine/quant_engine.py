"""
data_engine/quant_engine.py — Deterministic quantitative metrics engine.

Computes all §5.1 metrics per PRD:
  HV30, IV Rank, IV Percentile, Expected Move, 25-delta skew, 20-day momentum Z-Score.

Design rules (PRD §5.1 / CLAUDE.md):
  - Pure Python/NumPy/Pandas. No LLM. No network in unit tests.
  - All timestamps: timezone-aware UTC in storage; America/New_York only at display.
  - Typed Pydantic contracts for all inter-plane boundaries.
  - Money and quantities: decimal-safe (float64 with explicit decimal context in
    production; unit tests use fixture data that stays well clear of float precision cliffs).

FORMULA ASSUMPTIONS (documented per CLAUDE.md "do not invent"):
  1. IV Rank & Percentile: same 252-day (1 trading year) lookback window.
     - IV Rank  = (current_iv - min_iv_252d) / (max_iv_252d - min_iv_252d) * 100
     - IV Percentile = % of days in 252d where IV(t) < current_iv
  2. Expected Move: EM% = IV_annualized * sqrt(DTE / 365)
     (standard practitioner approximation; exact straddle-cost needs chain data)
  3. 25-delta skew: skew = IV(25Δ_put) / IV(ATM_put) - 1
     (positive = OTM puts more expensive than ATM = typical equity skew)
  4. Momentum Z-Score: 20-day simple returns, z-scored against 20-day rolling μ/σ.

All assumptions are explicit and testable; change the constants in QuantEngine.__init__
if the team decides to revise them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("aeroquant.quant_engine")

SCHEMA_VERSION = "2.6.0"


# ---------------------------------------------------------------------------
# Contracts — typed Pydantic models crossing plane boundaries
# ---------------------------------------------------------------------------

class DataQuality(str, Enum):
    """Evidence quality flags — mirrors EvidenceEnvelope.fallback_tier semantics."""

    PRIMARY = "primary"      # Live feed, no known issues
    STALE = "stale"         # Cached, >5 min old
    ESTIMATED = "estimated" # Calculated from proxy (e.g. SPY → XSP)
    UNAVAILABLE = "unavailable"


class MarketDataBundle(BaseModel):
    """
    Input bundle to QuantEngine.compute().
    All prices in dollars. All timestamps tz-aware UTC.
    """

    model_config = {"arbitrary_types_allowed": True}

    symbol: str = Field(description="Ticker symbol, e.g. XSP, SPY")
    spot_price: float = Field(gt=0, description="Current spot/futures price")

    # Historical closes — used for HV30 and momentum Z-Score
    # Must be sorted ascending by date, index = datetime (UTC)
    historical_closes: pd.Series = Field(
        description="Daily close prices, sorted ascending by date. "
                    "Index must be tz-aware datetime. "
                    "Min 31 rows needed for HV30; 252 rows for full IV Rank/Percentile."
    )

    # IV time series — daily ATM IV values, same index as historical_closes
    # Used for IV Rank, IV Percentile, and Expected Move
    historical_iv: pd.Series = Field(
        description="Daily ATM implied volatility (annualized, e.g. 0.18 = 18%), "
                    "same index as historical_closes. "
                    "Min 31 rows for current IV; 252 rows for rank/percentile."
    )

    # Current option chain snapshot — used for Expected Move and skew
    # If not available, compute() falls back to the IV-based approximation
    atm_call_price: float | None = Field(default=None, gt=0, description="ATM call mid price")
    atm_put_price: float | None = Field(default=None, gt=0, description="ATM put mid price")
    iv_atm: float | None = Field(default=None, ge=0, le=5, description="ATM IV (annualized)")
    iv_25_delta_put: float | None = Field(
        default=None, ge=0, le=5,
        description="25-delta put IV (annualized). If None, skew = None (no chain data)."
    )
    dte: int | None = Field(default=None, ge=1, le=730, description="Days to expiration")
    open_interest: int | None = Field(default=None, ge=0, description="Open interest at ATM")

    # Quality metadata
    iv_quality: DataQuality = Field(default=DataQuality.PRIMARY)
    spot_quality: DataQuality = Field(default=DataQuality.PRIMARY)
    chain_quality: DataQuality = Field(default=DataQuality.PRIMARY)

    @field_validator("historical_closes", "historical_iv", mode="before")
    @classmethod
    def _ensure_datetime_index(cls, v: pd.Series | list) -> pd.Series:
        if isinstance(v, list):
            v = pd.Series(v)
        if not isinstance(v.index, pd.DatetimeIndex):
            raise ValueError("historical_closes / historical_iv must have DatetimeIndex")
        if v.index.tz is None:
            raise ValueError("historical_closes / historical_iv index must be tz-aware (UTC)")
        return v.sort_index()

    @field_validator("atm_call_price", "atm_put_price", mode="before")
    @classmethod
    def _none_to_float(cls, v):
        return v if v is not None else None


class QuantMetrics(BaseModel):
    """
    Output of QuantEngine.compute() — all §5.1 deterministic metrics.

    Units:
      - HV, IV: annualized decimal (e.g. 0.18 = 18%)
      - EM_pct: percentage (e.g. 1.5 = 1.5%)
      - Skew: decimal ratio (e.g. 0.05 = ATM 5% higher IV than 25-delta put)
      - Momentum_z: z-score (dimensionless)
      - DTE: calendar days
    """

    model_config = {"arbitrary_types_allowed": True}

    schema_version: str = Field(default=SCHEMA_VERSION)
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of computation"
    )

    # --- HV30 ---
    hv30: float = Field(ge=0, le=5, description="30-day historical volatility (annualized)")
    hv30_quality: DataQuality

    # --- IV Rank ---
    iv_rank: float = Field(ge=0, le=100, description="IV rank 0–100 (252-day lookback)")
    iv_rank_quality: DataQuality

    # --- IV Percentile ---
    iv_percentile: float = Field(ge=0, le=100, description="IV percentile 0–100 (252-day lookback)")
    iv_percentile_quality: DataQuality

    # --- Expected Move ---
    em_pct: float = Field(ge=0, description="Expected move as % of spot")
    em_method: Literal["chain", "iv_approximation"] = Field(
        description="'chain' = (ATM_call + ATM_put) / spot × 100; "
                    "'iv_approximation' = IV × √(DTE/365)"
    )
    em_quality: DataQuality

    # --- 25-delta Skew ---
    skew_25_delta: float | None = Field(
        default=None,
        description="25-delta put skew: IV(25Δ_put)/IV(ATM) - 1. "
                    "Positive = typical equity skew (OTM puts expensive). "
                    "None if chain data unavailable."
    )
    skew_quality: DataQuality

    # --- Momentum Z-Score ---
    momentum_zscore_20d: float = Field(
        description="20-day momentum Z-score: (ret_20d - μ_20d) / σ_20d"
    )
    momentum_quality: DataQuality

    # --- Meta ---
    iv_rank_lookback_days: int = Field(default=252, description="Trading days for IV Rank/Percentile")
    hv_lookback_days: int = Field(default=30, description="Trading days for HV30")
    momentum_lookback_days: int = Field(default=20, description="Days for momentum Z-Score")

    # Derived convenience fields (not from PRD but used by Risk Manager)
    iv_high_regime: bool = Field(
        description="True if IV Rank >= 60 AND IV Percentile >= 60 — "
                    "strong mandate to authorize short-premium per PRD §5.1"
    )
    hv_vs_iv_ratio: float | None = Field(
        default=None,
        description="HV30 / IV_annualized. Values << 1 support VRP harvesting. None if IV unavailable."
    )

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class EvidenceEnvelope(BaseModel):
    """
    Wraps QuantMetrics with provenance metadata for downstream agents.

    This is the canonical output of the data plane — agents receive EvidenceEnvelopes,
    never raw data. Enables audit, fallbacks, and telemetry.
    """

    model_config = {"arbitrary_types_allowed": True}

    metrics: QuantMetrics
    source_symbol: str
    snapshot_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    fallback_tier: Literal["primary", "stale", "estimated", "unavailable"] = "primary"
    quality_flags: list[str] = Field(default_factory=list)

    def is_usable(self) -> bool:
        """Returns False if the underlying metrics are from an unavailable source."""
        return self.fallback_tier != "unavailable"

    def regime_signal(self) -> str:
        """
        Quick regime classification for LLM agents.
        Returns one of: "high_iv", "low_iv", "neutral", "unknown".
        """
        if not self.is_usable():
            return "unknown"
        m = self.metrics
        if m.iv_rank >= 60 and m.iv_percentile >= 60:
            return "high_iv"
        elif m.iv_rank <= 30 and m.iv_percentile <= 30:
            return "low_iv"
        return "neutral"


# ---------------------------------------------------------------------------
# Quant Engine
# ---------------------------------------------------------------------------

class QuantEngine:
    """
    Deterministic quantitative metrics calculator.

    Stateless: every compute() call is pure given the same MarketDataBundle.
    No LLM. No Alpaca API. No network in tests.

    Parameters (all configurable via __init__ for testability):
      iv_rank_lookback: trading days for IV Rank/Percentile (default 252)
      hv_lookback: trading days for HV30 (default 30)
      momentum_lookback: days for Z-Score (default 20)
      iv_rank_entry_threshold: IV Rank/Percentile threshold per PRD §5.1 (default 60)
    """

    def __init__(
        self,
        iv_rank_lookback: int = 252,
        hv_lookback: int = 30,
        momentum_lookback: int = 20,
        iv_rank_entry_threshold: float = 60.0,
    ) -> None:
        self.iv_rank_lookback = iv_rank_lookback
        self.hv_lookback = hv_lookback
        self.momentum_lookback = momentum_lookback
        self.iv_rank_entry_threshold = iv_rank_entry_threshold

    def compute(self, data: MarketDataBundle) -> QuantMetrics:
        """
        Compute all §5.1 metrics from a MarketDataBundle.

        Returns QuantMetrics. Raises ValueError if required lookback lengths are insufficient.
        """
        closes = data.historical_closes
        ivs = data.historical_iv

        # --- HV30 ---
        hv30, hv30_quality = self._compute_hv30(closes)

        # --- IV Rank & Percentile ---
        iv_rank, iv_rank_quality = self._compute_iv_rank(ivs)
        iv_pct, iv_pct_quality = self._compute_iv_percentile(ivs)

        # --- Expected Move ---
        em_pct, em_method, em_quality = self._compute_expected_move(data)

        # --- 25-delta Skew ---
        skew_val, skew_quality = self._compute_skew(data)

        # --- Momentum Z-Score ---
        mom_z, mom_quality = self._compute_momentum_zscore(closes)

        # --- Derived fields ---
        current_iv = ivs.iloc[-1] if len(ivs) > 0 else None
        iv_high = (
            bool(iv_rank >= self.iv_rank_entry_threshold)
            and bool(iv_pct >= self.iv_rank_entry_threshold)
        )
        hv_vs_iv = (hv30 / current_iv) if current_iv and current_iv > 0 else None

        return QuantMetrics(
            hv30=hv30,
            hv30_quality=hv30_quality,
            iv_rank=iv_rank,
            iv_rank_quality=iv_rank_quality,
            iv_percentile=iv_pct,
            iv_percentile_quality=iv_pct_quality,
            em_pct=em_pct,
            em_method=em_method,
            em_quality=em_quality,
            skew_25_delta=skew_val,
            skew_quality=skew_quality,
            momentum_zscore_20d=mom_z,
            momentum_quality=mom_quality,
            iv_rank_lookback_days=self.iv_rank_lookback,
            hv_lookback_days=self.hv_lookback,
            momentum_lookback_days=self.momentum_lookback,
            iv_high_regime=iv_high,
            hv_vs_iv_ratio=hv_vs_iv,
        )

    # -------------------------------------------------------------------------
    # HV30: Historical Volatility 30-day
    # -------------------------------------------------------------------------

    def _compute_hv30(
        self, closes: pd.Series
    ) -> tuple[float, DataQuality]:
        """
        HV30 = std(ln(Pt / Pt-1)) × √252

        Requires at least self.hv_lookback + 1 closes.
        Quality flag:
          - PRIMARY if we have >= 252 rows (full year)
          - STALE if 31–251 rows
          - ESTIMATED if 21–30 rows (partial window)
        """
        required = self.hv_lookback + 1
        n = len(closes)
        if n < 21:  # practical minimum — can't even compute 20-day returns
            raise ValueError(
                f"HV30 needs at least 21 closes; got {n}. "
                "Use estimated=True to suppress this error."
            )

        # Use available data up to lookback (don't require exact 30 if we have more)
        lookback = min(n - 1, self.hv_lookback)
        prices = closes.iloc[-lookback - 1:] if n > self.hv_lookback else closes
        log_returns = np.log(prices / prices.shift(1)).dropna()

        if len(log_returns) < 2:
            raise ValueError(f"Not enough data to compute HV{lookback}: {len(log_returns)} returns")

        hv = float(log_returns.std(ddof=1) * np.sqrt(252))

        quality = DataQuality.PRIMARY
        if n < 252:
            quality = DataQuality.STALE if n >= 31 else DataQuality.ESTIMATED

        return hv, quality

    # -------------------------------------------------------------------------
    # IV Rank
    # -------------------------------------------------------------------------

    def _compute_iv_rank(
        self, ivs: pd.Series
    ) -> tuple[float, DataQuality]:
        """
        IV Rank = (current_iv - min_iv_lookback) / (max_iv_lookback - min_iv_lookback) × 100

        Requires at least 31 rows for a meaningful rank.
        """
        n = len(ivs)
        lookback = min(n, self.iv_rank_lookback)

        if lookback < 31:
            raise ValueError(
                f"IV Rank needs at least 31 rows of IV history; got {lookback}. "
                "Pass full 252-day history for primary quality."
            )

        iv_window = ivs.iloc[-lookback:]
        current_iv = iv_window.iloc[-1]
        min_iv = iv_window.min()
        max_iv = iv_window.max()

        if max_iv == min_iv:
            rank = 50.0  # flat — all values identical
        else:
            rank = (current_iv - min_iv) / (max_iv - min_iv) * 100

        rank = float(np.clip(rank, 0.0, 100.0))

        quality = DataQuality.PRIMARY if lookback >= 252 else DataQuality.STALE
        return rank, quality

    # -------------------------------------------------------------------------
    # IV Percentile
    # -------------------------------------------------------------------------

    def _compute_iv_percentile(
        self, ivs: pd.Series
    ) -> tuple[float, DataQuality]:
        """
        IV Percentile = % of days in lookback where IV(t) < current_iv

        Uses the same 252-day lookback as IV Rank.
        """
        n = len(ivs)
        lookback = min(n, self.iv_rank_lookback)

        if lookback < 31:
            raise ValueError(
                f"IV Percentile needs at least 31 rows; got {lookback}."
            )

        iv_window = ivs.iloc[-lookback:]
        current_iv = iv_window.iloc[-1]

        # Count days strictly below current IV (exclude today itself)
        prior_ivs = iv_window.iloc[:-1]
        if len(prior_ivs) == 0:
            pct = 50.0
        else:
            pct = float((prior_ivs < current_iv).sum() / len(prior_ivs) * 100)

        quality = DataQuality.PRIMARY if lookback >= 252 else DataQuality.STALE
        return pct, quality

    # -------------------------------------------------------------------------
    # Expected Move
    # -------------------------------------------------------------------------

    def _compute_expected_move(
        self, data: MarketDataBundle
    ) -> tuple[float, Literal["chain", "iv_approximation"], DataQuality]:
        """
        Primary: EM% = (ATM_call_mid + ATM_put_mid) / spot × 100
        Fallback: EM% = IV × √(DTE / 365) × 100

        The IV-based approximation is the standard practitioner formula.
        The chain-based EM is more accurate when option prices are available.
        """
        if (
            data.atm_call_price is not None
            and data.atm_put_price is not None
            and data.spot_price > 0
            and data.chain_quality != DataQuality.UNAVAILABLE
        ):
            em_pct = (data.atm_call_price + data.atm_put_price) / data.spot_price * 100
            return float(em_pct), "chain", data.chain_quality

        # Fallback: IV-based approximation
        if data.iv_atm is None or data.dte is None or data.dte <= 0:
            raise ValueError(
                "Expected Move cannot be computed: no chain data, and either "
                "iv_atm or dte is missing. Provide ATM option prices or IV + DTE."
            )

        iv_frac = data.iv_atm * np.sqrt(data.dte / 365.0)
        em_pct = iv_frac * 100
        quality = DataQuality.ESTIMATED if data.chain_quality == DataQuality.PRIMARY else data.chain_quality

        logger.info(
            "Expected Move using IV approximation: IV=%.2f%%, DTE=%d, EM%%=%.2f%%",
            data.iv_atm * 100, data.dte, em_pct
        )
        return float(em_pct), "iv_approximation", quality

    # -------------------------------------------------------------------------
    # 25-delta Volatility Skew
    # -------------------------------------------------------------------------

    def _compute_skew(
        self, data: MarketDataBundle
    ) -> tuple[float | None, DataQuality]:
        """
        25-delta skew = IV(25Δ_put) / IV(ATM) - 1

        Positive skew = typical equity skew (OTM puts more expensive than ATM).
        Returns None if chain data not available (no obligation to compute skew without data).
        """
        if data.iv_25_delta_put is None or data.iv_atm is None:
            return None, DataQuality.ESTIMATED  # chain unavailable, not an error

        if data.iv_atm <= 0:
            return None, DataQuality.ESTIMATED

        skew = data.iv_25_delta_put / data.iv_atm - 1
        return float(skew), data.chain_quality

    # -------------------------------------------------------------------------
    # 20-day Momentum Z-Score
    # -------------------------------------------------------------------------

    def _compute_momentum_zscore(
        self, closes: pd.Series
    ) -> tuple[float, DataQuality]:
        """
        20-day momentum Z-Score:
          ret_20d = (P_t / P_{t-20}) - 1
          z = (ret_20d - rolling_mean_20d) / rolling_std_20d

        Requires at least 40 closes for the rolling window.
        """
        n = len(closes)
        required = self.momentum_lookback * 2  # need lookback window + 20d return period

        if n < required:
            raise ValueError(
                f"Momentum Z-Score needs at least {required} closes; got {n}. "
                f"Need {self.momentum_lookback} for the rolling window "
                f"and {self.momentum_lookback} for the return calculation."
            )

        ret_20d = closes.pct_change(self.momentum_lookback)

        # Rolling 20-day mean and std of returns
        rolling_mean = closes.pct_change().rolling(self.momentum_lookback).mean()
        rolling_std = closes.pct_change().rolling(self.momentum_lookback).std(ddof=1)

        # Current 20d return
        current_ret = ret_20d.iloc[-1]
        current_mean = rolling_mean.iloc[-1]
        current_std = rolling_std.iloc[-1]

        if current_std == 0 or np.isnan(current_std) or np.isnan(current_mean):
            # Flat period — no momentum signal
            zscore = 0.0
        else:
            zscore = (current_ret - current_mean) / current_std

        zscore = float(np.clip(zscore, -10.0, 10.0))  # clip extreme values

        quality = DataQuality.PRIMARY if n >= 252 else DataQuality.STALE
        return zscore, quality
