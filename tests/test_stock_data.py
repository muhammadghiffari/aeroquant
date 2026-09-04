from datetime import date

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.timeframe import TimeFrame

from data_engine import stock_data


def test_get_intraday_bars_returns_sorted_ohlcv(monkeypatch):
    raw = pd.DataFrame(
        {
            "open": [2.0, 1.0],
            "high": [2.2, 1.2],
            "low": [1.8, 0.8],
            "close": [2.1, 1.1],
            "volume": [20, 10],
        },
        index=pd.to_datetime(["2026-08-29 14:02", "2026-08-29 14:01"]),
    )
    seen = {}

    monkeypatch.setattr(
        stock_data.alpaca_client,
        "safe",
        lambda name, fn, req: seen.update(name=name, request=req) or type("R", (), {"df": raw})(),
    )

    result = stock_data.get_intraday_bars("SPY", minutes=90)

    assert list(result["close"]) == [1.1, 2.1]
    assert result.index.is_monotonic_increasing
    assert seen["name"] == "get_stock_bars"
    assert seen["request"].feed == DataFeed.IEX


def test_get_daily_bars_uses_exchange_market_date(monkeypatch):
    raw = pd.DataFrame(
        {
            "open": [2.0],
            "high": [2.2],
            "low": [1.8],
            "close": [2.1],
            "volume": [20],
        },
        index=pd.to_datetime(["2026-09-01"]),
    )
    seen = {}

    class MarketConfig:
        @staticmethod
        def market_date():
            return date(2026, 9, 1)

    monkeypatch.setattr(stock_data, "config", MarketConfig)
    monkeypatch.setattr(
        stock_data.alpaca_client,
        "safe",
        lambda name, fn, req: seen.update(name=name, request=req) or type("R", (), {"df": raw})(),
    )

    stock_data.get_daily_bars("SPY", days=400)

    assert seen["request"].start.date() == date(2025, 7, 28)


def test_get_hourly_bars_returns_historical_ohlcv(monkeypatch):
    raw = pd.DataFrame(
        {
            "open": [2.0, 1.0],
            "high": [2.2, 1.2],
            "low": [1.8, 0.8],
            "close": [2.1, 1.1],
            "volume": [20, 10],
        },
        index=pd.to_datetime(["2026-08-29 14:00", "2026-08-29 13:00"]),
    )
    seen = {}
    monkeypatch.setattr(
        stock_data.alpaca_client,
        "safe",
        lambda name, fn, req: seen.update(name=name, request=req) or type("R", (), {"df": raw})(),
    )

    result = stock_data.get_hourly_bars("SPY", days=30)

    assert list(result["close"]) == [1.1, 2.1]
    assert result.index.is_monotonic_increasing
    assert str(seen["request"].timeframe) == str(TimeFrame.Hour)
    assert seen["request"].feed == DataFeed.IEX
