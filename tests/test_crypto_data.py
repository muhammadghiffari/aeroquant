from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from data_engine import crypto_data


def test_get_crypto_intraday_bars_normalizes_multiindex_response(monkeypatch):
    index = pd.MultiIndex.from_tuples(
        [("BTC/USD", datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc)),
         ("BTC/USD", datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc))],
        names=["symbol", "timestamp"],
    )
    raw = pd.DataFrame(
        {"open": [101.0, 100.0], "high": [102.0, 101.0], "low": [100.0, 99.0],
         "close": [101.5, 100.5], "volume": [20.0, 10.0]}, index=index
    )
    seen = {}
    monkeypatch.setattr(
        crypto_data.alpaca_client,
        "safe",
        lambda name, fn, request: seen.update(name=name, request=request)
        or SimpleNamespace(df=raw),
    )
    monkeypatch.setattr(
        crypto_data.alpaca_client,
        "crypto_data_client",
        lambda: SimpleNamespace(get_crypto_bars=lambda request: None),
    )

    result = crypto_data.get_crypto_intraday_bars("BTC/USD", minutes=90)

    assert seen["name"] == "get_crypto_bars"
    assert list(result["close"]) == [100.5, 101.5]
    assert result.index.is_monotonic_increasing
