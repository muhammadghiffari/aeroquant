"""
tests/conftest.py — Shared pytest fixtures for deterministic tests.

All fixtures are pure data — no network, no LLM, no Alpaca.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np
import pandas as pd
import pytest

from data_engine.quant_engine import (
    MarketDataBundle,
    QuantEngine,
    DataQuality,
)
from agents.risk_manager import (
    TradeProposal,
    SpreadSpec,
    LegSpec,
    RiskManager,
    RiskParams,
    TradeSide,
)


# ---------------------------------------------------------------------------
# Quant Engine Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine() -> QuantEngine:
    """Default QuantEngine with standard parameters."""
    return QuantEngine(
        iv_rank_lookback=252,
        hv_lookback=30,
        momentum_lookback=20,
        iv_rank_entry_threshold=60.0,
    )


def _make_prices(
    n: int,
    start_price: float = 100.0,
    daily_return_mu: float = 0.0,
    daily_return_sigma: float = 0.01,
    seed: int = 42,
) -> pd.Series:
    """Generate realistic price series with known statistical properties."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=n,
        freq="D",
    )
    log_returns = rng.normal(daily_return_mu, daily_return_sigma, size=n)
    prices = start_price * np.exp(np.cumsum(log_returns))
    return pd.Series(prices, index=dates)


@pytest.fixture
def closes_30d() -> pd.Series:
    """30 days of daily closes (minimum for HV30)."""
    return _make_prices(31, start_price=100.0, daily_return_sigma=0.008, seed=0)


@pytest.fixture
def closes_252d() -> pd.Series:
    """252 days of daily closes (full year for IV Rank/Percentile)."""
    return _make_prices(252, start_price=100.0, daily_return_sigma=0.010, seed=1)


@pytest.fixture
def closes_50d() -> pd.Series:
    """50 days — enough for HV30 and momentum Z-score."""
    return _make_prices(50, start_price=100.0, daily_return_sigma=0.009, seed=2)


@pytest.fixture
def iv_series_flat() -> pd.Series:
    """252 days of flat IV (all 0.20 = 20%)."""
    dates = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=252,
        freq="D",
    )
    return pd.Series(0.20, index=dates)


@pytest.fixture
def iv_series_rising() -> pd.Series:
    """252 days of IV rising from 0.10 to 0.30 (rank ~66%, percentile ~66%)."""
    dates = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=252,
        freq="D",
    )
    ivs = np.linspace(0.10, 0.30, 252)
    return pd.Series(ivs, index=dates)


@pytest.fixture
def iv_series_high_current() -> pd.Series:
    """
    252 days of IV with current value at 90th percentile (rank ~90).
    Use case: IV Rank > 60 → strong mandate for short-premium.
    """
    dates = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=252,
        freq="D",
    )
    # 226 days at 0.15, 26 days at 0.30 — current is the last day at 0.30
    low = np.full(226, 0.15)
    high = np.full(26, 0.30)
    ivs = np.concatenate([low, high])
    return pd.Series(ivs, index=dates)


@pytest.fixture
def sample_bundle_252d(iv_series_high_current, closes_252d) -> MarketDataBundle:
    """Full 252-day bundle with rising IV."""
    return MarketDataBundle(
        symbol="XSP",
        spot_price=closes_252d.iloc[-1],
        historical_closes=closes_252d,
        historical_iv=iv_series_high_current,
        atm_call_price=None,
        atm_put_price=None,
        iv_atm=0.30,
        iv_25_delta_put=0.33,
        dte=3,
        open_interest=1000,
        iv_quality=DataQuality.PRIMARY,
        spot_quality=DataQuality.PRIMARY,
        chain_quality=DataQuality.PRIMARY,
    )


def make_complete_bundle(
    closes: pd.Series,
    ivs: pd.Series,
    *,
    symbol: str = "XSP",
    iv_atm: float = 0.20,
    dte: int = 3,
    iv_25_delta_put: float | None = None,
    atm_call: float | None = None,
    atm_put: float | None = None,
    chain_quality: DataQuality = DataQuality.PRIMARY,
) -> MarketDataBundle:
    """
    Helper to build a complete MarketDataBundle with all required fields.
    Use this instead of bare MarketDataBundle() in compute() tests.
    """
    return MarketDataBundle(
        symbol=symbol,
        spot_price=closes.iloc[-1],
        historical_closes=closes,
        historical_iv=ivs,
        iv_atm=iv_atm,
        dte=dte,
        iv_25_delta_put=iv_25_delta_put,
        atm_call_price=atm_call,
        atm_put_price=atm_put,
        chain_quality=chain_quality,
    )


# ---------------------------------------------------------------------------
# Risk Manager Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def risk_params() -> RiskParams:
    """Default risk parameters matching PRD §8.

    min_loss_pct=0.001 → $100 minimum loss on $100k account.
    This allows a default Iron Condor (max_loss ~$950) to pass,
    while a micro-trade ($2 max loss) still fails.
    """
    return RiskParams(min_loss_pct=0.001)


@pytest.fixture
def risk_manager(risk_params) -> RiskManager:
    """Default RiskManager with PRD §8 parameters."""
    return RiskManager(params=risk_params)


def make_leg(
    symbol: str = "XSP241205P00550000",
    action: Literal["BUY", "SELL"] = "SELL",
    quantity: int = 1,
    strike: float = 555.0,
    expiration: datetime | None = None,
    option_type: Literal["call", "put"] = "put",
    bid: float = 2.00,
    ask: float = 2.05,
) -> LegSpec:
    """Helper to create a LegSpec with defaults."""
    exp = expiration or (datetime(2024, 12, 5, tzinfo=timezone.utc))
    return LegSpec(
        symbol=symbol,
        action=action,
        quantity=quantity,
        strike=strike,
        expiration=exp,
        option_type=option_type,
        bid_price=bid,
        ask_price=ask,
        mid_price=(bid + ask) / 2,
    )


def make_iron_condor_proposal(
    *,
    max_loss_pct: float = 0.02,
    account_buying_power: float = 100_000.0,
    open_positions: int = 0,
    current_exposure_pct: float = 0.0,
    new_trade_exposure_pct: float = 0.0,
    daily_pnl_pct: float = 0.0,
    week_pnl_pct: float = 0.0,
    iv_high_regime: bool = True,
    momentum_zscore: float | None = 0.0,
    consecutive_rejections: int = 0,
    net_credit: float = 2.00,
    dte: int = 3,
) -> TradeProposal:
    """
    Standard Iron Condor proposal — valid by default, can override params.

    Spread: $10 wide per side (555/545 puts, 605/615 calls), 1 contract per side.
    Short legs: bid=2.00, ask=2.01 → spread_pct = 0.498% < 5% liquidity limit.
    Long legs: bid=0.05, ask=0.051 → spread_pct = 1.96% < 5% liquidity limit.
    Computed IC max_loss (1 contract per side): ~$1,996 — just under the $2,000 (2%) limit.
    """

    short_put = make_leg(
        symbol="XSP241205P00555000",
        action="SELL",
        quantity=1,
        strike=555.0,
        option_type="put",
        bid=2.00,
        ask=2.01,
    )
    long_put = make_leg(
        symbol="XSP241205P00545000",
        action="BUY",
        quantity=1,
        strike=545.0,
        option_type="put",
        bid=0.05,
        ask=0.051,
    )
    short_call = make_leg(
        symbol="XSP241205C00605000",
        action="SELL",
        quantity=1,
        strike=605.0,
        option_type="call",
        bid=2.00,
        ask=2.01,
    )
    long_call = make_leg(
        symbol="XSP241205C00615000",
        action="BUY",
        quantity=1,
        strike=615.0,
        option_type="call",
        bid=0.05,
        ask=0.051,
    )

    # The fixture's spread.max_loss is a LLM-supplied value used only for the
    # discrepancy warning in _check_max_loss. The authoritative calculation is
    # _compute_max_loss_from_legs() which uses the actual leg strikes and quantities.
    # Set total_max_loss to an extreme value so the discrepancy warning fires
    # (proving the LLM-supplied value is not trusted) while the computed value
    # (~$1,996-$1,998 for a 1-contract IC) stays within the 2% limit.
    total_max_loss = 500_000.0   # deliberately extreme LLM-supplied value

    spread = SpreadSpec(
        legs=[short_put, long_put, short_call, long_call],
        dte=dte,
        net_credit=net_credit,
        max_credit=net_credit,
        max_loss=total_max_loss,
        side=TradeSide.IRON_CONDOR,
    )

    return TradeProposal(
        proposal_id="test-prop-001",
        spread=spread,
        account_buying_power=account_buying_power,
        account_equity=account_buying_power,
        open_positions=open_positions,
        current_exposure_pct=current_exposure_pct,
        new_trade_exposure_pct=new_trade_exposure_pct,
        daily_realized_pnl_pct=daily_pnl_pct,
        week_realized_pnl_pct=week_pnl_pct,
        iv_high_regime=iv_high_regime,
        momentum_zscore=momentum_zscore,
        consecutive_rejections=consecutive_rejections,
    )
