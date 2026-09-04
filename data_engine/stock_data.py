"""Underlying stock data helpers."""
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from data_engine import alpaca_client

log = logging.getLogger(__name__)


def get_daily_bars(symbol: str, days: int = 400) -> pd.DataFrame:
    """Return daily OHLCV bars as DataFrame indexed by date (oldest first)."""
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=config.market_date() - timedelta(days=days),
    )
    resp = alpaca_client.safe("get_stock_bars", alpaca_client.stock_data_client().get_stock_bars, req)
    df = resp.df
    if df.empty:
        raise ValueError(f"No stock bars returned for {symbol}")
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol.upper(), level=0)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def get_hourly_bars(symbol: str, days: int = 60) -> pd.DataFrame:
    """Return historical one-hour OHLCV bars in ascending UTC order."""
    req = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=TimeFrame.Hour,
        start=config.market_date() - timedelta(days=days),
        feed=DataFeed.IEX,
    )
    resp = alpaca_client.safe(
        "get_stock_bars_hourly", alpaca_client.stock_data_client().get_stock_bars, req
    )
    df = resp.df
    if df.empty:
        raise ValueError(f"No hourly stock bars returned for {symbol}")
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol.upper(), level=0)
    df = df.copy()
    index = pd.to_datetime(df.index)
    df.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return df.sort_index()


def get_spot_price(symbol: str) -> float:
    """Latest available close as spot proxy."""
    bars = get_daily_bars(symbol, days=10)
    return float(bars["close"].iloc[-1])


def get_intraday_bars(symbol: str, minutes: int = 240) -> pd.DataFrame:
    """Return recent one-minute OHLCV bars in ascending UTC order."""
    if minutes < 30:
        raise ValueError("intraday lookback must be at least 30 minutes")
    end = datetime.now(timezone.utc)
    req = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=TimeFrame.Minute,
        start=end - timedelta(minutes=minutes),
        end=end,
        feed=DataFeed.IEX,
    )
    resp = alpaca_client.safe(
        "get_stock_bars", alpaca_client.stock_data_client().get_stock_bars, req
    )
    df = resp.df
    if df.empty:
        raise ValueError(f"No intraday stock bars returned for {symbol}")
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol.upper(), level=0)
    df = df.copy()
    index = pd.to_datetime(df.index)
    df.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return df.sort_index()
