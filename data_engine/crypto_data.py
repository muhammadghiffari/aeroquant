"""Crypto market-data helpers used by the BTC context layer."""
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

from data_engine import alpaca_client


def get_crypto_intraday_bars(symbol: str = "BTC/USD", minutes: int = 240) -> pd.DataFrame:
    """Return recent one-minute crypto bars in ascending UTC order."""
    if minutes < 60:
        raise ValueError("crypto lookback must be at least 60 minutes")
    symbol = symbol.upper()
    end = datetime.now(timezone.utc)
    request = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=end - timedelta(minutes=minutes),
        end=end,
    )
    response = alpaca_client.safe(
        "get_crypto_bars", alpaca_client.crypto_data_client().get_crypto_bars, request
    )
    frame = response.df
    if frame.empty:
        raise ValueError(f"No crypto bars returned for {symbol}")
    if isinstance(frame.index, pd.MultiIndex):
        frame = frame.xs(symbol, level=0)
    frame = frame.copy()
    index = pd.to_datetime(frame.index)
    frame.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return frame.sort_index()
