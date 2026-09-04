from datetime import datetime, timezone

import pandas as pd
import pytest

from backtest_engine import (
    calculate_metrics,
    load_json_file,
    normalize_stock_bars,
    normalize_option_quotes,
    run_options_backtest,
    simulate_underlying_signals,
)


def test_normalize_stock_bars_returns_sorted_numeric_frame():
    payload = {
        "symbol": "NVDA",
        "bars": [
            {"t": "2026-08-28T04:00:00Z", "o": "20", "h": 22, "l": 19, "c": 21, "v": 100},
            {"t": "2026-08-27T04:00:00Z", "o": 18, "h": 21, "l": 17, "c": 20, "v": 90},
        ],
    }

    result = normalize_stock_bars(payload)

    assert list(result.index) == [
        pd.Timestamp("2026-08-27", tz=timezone.utc),
        pd.Timestamp("2026-08-28", tz=timezone.utc),
    ]
    assert result.loc[pd.Timestamp("2026-08-27", tz=timezone.utc), "close"] == 20.0
    assert result["volume"].dtype.kind in "fi"


def test_load_json_file_accepts_windows_utf8_bom(tmp_path):
    path = tmp_path / "cli.json"
    path.write_bytes(b"\xef\xbb\xbf{\"bars\": []}")

    assert load_json_file(path) == {"bars": []}


def test_calculate_metrics_uses_daily_returns_and_sample_sharpe():
    equity = pd.Series(
        [100.0, 105.0, 102.0, 110.0],
        index=pd.date_range("2026-01-01", periods=4, tz="UTC"),
    )

    result = calculate_metrics(equity, periods_per_year=252)

    assert result["total_return"] == pytest.approx(0.1)
    assert result["max_drawdown"] == pytest.approx(102 / 105 - 1)
    assert result["final_equity"] == 110.0
    assert result["trading_days"] == 3


def test_underlying_signal_simulation_fills_on_next_close_without_lookahead():
    index = pd.date_range("2026-01-01", periods=6, tz="UTC")
    bars = pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100, 102, 104, 103, 105, 107],
            "volume": [100] * 6,
        },
        index=index,
    )

    result = simulate_underlying_signals(bars, fast_window=2, slow_window=3)

    assert result["trades"][0]["signal_time"] < result["trades"][0]["fill_time"]
    assert result["trades"][0]["fill_price"] == 103.0
    assert result["equity"].iloc[1] == 100_000.0


def test_normalize_option_quotes_preserves_point_in_time_bid_ask():
    payload = {"quotes": [
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYC", "bp": "2.00", "ap": "2.10"},
        {"t": "2026-01-01T14:30:00Z", "symbol": "SPYC", "bp": 1.90, "ap": 2.05},
    ]}

    result = normalize_option_quotes(payload)

    assert list(result["symbol"]) == ["SPYC", "SPYC"]
    assert list(result["bid"]) == [1.90, 2.00]
    assert list(result["ask"]) == [2.05, 2.10]
    assert result.index[0] < result.index[1]


def test_options_backtest_uses_next_quote_bid_ask_and_tp_without_lookahead():
    quotes = normalize_option_quotes({"quotes": [
        {"t": "2026-01-01T14:30:00Z", "symbol": "SPYC", "bp": 1.90, "ap": 2.00},
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYC", "bp": 2.70, "ap": 2.80},
        {"t": "2026-01-01T14:32:00Z", "symbol": "SPYC", "bp": 3.80, "ap": 3.90},
    ]})
    signals = [{
        "timestamp": "2026-01-01T14:30:00Z",
        "symbol": "SPYC",
        "strategy_type": "LONG_CALL",
        "actionable": True,
    }]

    result = run_options_backtest(
        quotes, signals, strategy_type="LONG_CALL", take_profit_pct=0.33,
        stop_loss_pct=0.50, slippage_per_share=0.01, fee_per_contract=1.0,
    )

    assert result["metrics"]["fill_rate"] == 1.0
    assert result["trades"][0]["entry_price"] == 2.81
    assert result["trades"][0]["exit_price"] == 3.79
    assert result["trades"][0]["exit_reason"] == "take_profit"
    assert result["trades"][0]["pnl"] == 97.0
    assert result["metrics"]["sample_size"] == 1
    assert result["metrics"]["expectancy"] == 97.0


def test_options_backtest_reports_call_and_put_arms_separately_and_counts_nonfill():
    quotes = normalize_option_quotes({"quotes": [
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYC", "bp": 2.0, "ap": 2.1},
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYP", "bp": 2.0, "ap": 2.1},
    ]})
    signals = [
        {"timestamp": "2026-01-01T14:30:00Z", "symbol": "SPYC", "strategy_type": "LONG_CALL", "actionable": True},
        {"timestamp": "2026-01-01T14:32:00Z", "symbol": "SPYP", "strategy_type": "LONG_PUT", "actionable": True},
    ]

    result = run_options_backtest(quotes, signals)

    assert set(result["by_strategy"]) == {"LONG_CALL", "LONG_PUT"}
    assert result["by_strategy"]["LONG_CALL"]["metrics"]["fill_rate"] == 1.0
    assert result["by_strategy"]["LONG_PUT"]["metrics"]["fill_rate"] == 0.0


def test_combined_options_backtest_exposes_portfolio_metrics():
    quotes = normalize_option_quotes({"quotes": [
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYC", "bp": 2.0, "ap": 2.1},
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYP", "bp": 1.0, "ap": 1.1},
        {"t": "2026-01-01T14:32:00Z", "symbol": "SPYC", "bp": 3.0, "ap": 3.1},
        {"t": "2026-01-01T14:32:00Z", "symbol": "SPYP", "bp": 0.5, "ap": 0.6},
    ]})
    signals = [
        {"timestamp": "2026-01-01T14:30:00Z", "symbol": "SPYC", "strategy_type": "LONG_CALL"},
        {"timestamp": "2026-01-01T14:30:00Z", "symbol": "SPYP", "strategy_type": "LONG_PUT"},
    ]

    result = run_options_backtest(quotes, signals)

    assert result["metrics"]["sample_size"] == 2
    assert len(result["trades"]) == 2


def test_options_backtest_closes_at_signal_expiry_when_no_price_exit():
    quotes = normalize_option_quotes({"quotes": [
        {"t": "2026-01-01T14:31:00Z", "symbol": "SPYC", "bp": 2.0, "ap": 2.1},
        {"t": "2026-01-02T14:31:00Z", "symbol": "SPYC", "bp": 2.0, "ap": 2.1},
    ]})
    signals = [{
        "timestamp": "2026-01-01T14:30:00Z",
        "symbol": "SPYC",
        "strategy_type": "LONG_CALL",
        "expiry": "2026-01-01",
    }]

    result = run_options_backtest(quotes, signals, strategy_type="LONG_CALL")

    assert result["trades"][0]["exit_reason"] == "expiry"
    assert result["trades"][0]["exit_time"].startswith("2026-01-01")
