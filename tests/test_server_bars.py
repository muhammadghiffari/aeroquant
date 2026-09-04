import json

import pandas as pd

import server


def test_api_bars_returns_hourly_ohlcv_for_chart(monkeypatch):
    frame = pd.DataFrame(
        {"open": [1.0, 2.0], "high": [1.2, 2.2], "low": [0.8, 1.8], "close": [1.1, 2.1], "volume": [10, 20]},
        index=pd.to_datetime(["2026-09-01 13:00", "2026-09-01 14:00"], utc=True),
    )
    monkeypatch.setattr(server, "get_hourly_bars", lambda _symbol, days: frame)

    response = server.api_bars("AAPL", timeframe="1H", days=10)
    payload = json.loads(response.body)

    assert payload["timeframe"] == "1H"
    assert payload["bars"][0]["close"] == 1.1
    assert payload["bars"][1]["volume"] == 20


def test_dashboard_names_proxy_and_shadow_modes():
    assert "GREEN_PROXY" in server.HTML_PAGE
    assert "SHADOW_ONLY" in server.HTML_PAGE
