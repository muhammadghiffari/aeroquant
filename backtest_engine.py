"""Deterministic helpers for underlying diagnostics and options research."""
from __future__ import annotations

from typing import Any
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_json_file(path: str | Path) -> dict[str, Any]:
    """Read JSON emitted by the Windows Alpaca CLI, including a UTF-8 BOM."""
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def normalize_stock_bars(payload: dict[str, Any]) -> pd.DataFrame:
    """Normalize one Alpaca CLI stock-bars response into an OHLCV frame."""
    rows = payload.get("bars", [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    required = {"t", "o", "h", "l", "c", "v"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing Alpaca bar fields: {sorted(missing)}")

    frame = frame.rename(
        columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    return frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]].sort_index()


def calculate_metrics(equity: pd.Series, periods_per_year: int = 252) -> dict[str, float | int]:
    """Calculate standard daily-equity metrics with sample volatility."""
    values = pd.to_numeric(equity, errors="coerce").dropna()
    if values.empty or (values <= 0).any():
        raise ValueError("equity must contain positive numeric values")

    returns = values.pct_change().dropna()
    trading_days = int(len(returns))
    total_return = float(values.iloc[-1] / values.iloc[0] - 1.0)
    running_max = values.cummax()
    drawdowns = values / running_max - 1.0
    max_drawdown = float(drawdowns.min())
    if trading_days:
        annualized_return = float((1.0 + total_return) ** (periods_per_year / trading_days) - 1.0)
    else:
        annualized_return = 0.0
    volatility = float(returns.std(ddof=1)) if trading_days > 1 else 0.0
    sharpe = float(np.sqrt(periods_per_year) * returns.mean() / volatility) if volatility > 0 else 0.0
    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "final_equity": float(values.iloc[-1]),
        "trading_days": trading_days,
    }


def simulate_underlying_signals(
    bars: pd.DataFrame,
    fast_window: int = 9,
    slow_window: int = 21,
    initial_equity: float = 100_000.0,
) -> dict[str, Any]:
    """Run a long-only crossover diagnostic using next-session-open fills.

    This is intentionally an underlying diagnostic, not an options PnL model.
    It exists to validate signal timing while option quote history is unavailable.
    """
    if fast_window >= slow_window:
        raise ValueError("fast_window must be less than slow_window")
    frame = bars.sort_index().copy()
    frame["fast"] = frame["close"].rolling(fast_window, min_periods=fast_window).mean()
    frame["slow"] = frame["close"].rolling(slow_window, min_periods=slow_window).mean()

    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    equity = pd.Series(initial_equity, index=[frame.index[0]], dtype=float) if not frame.empty else pd.Series(dtype=float)
    for index in range(1, len(frame) - 1):
        current = frame.iloc[index]
        previous = frame.iloc[index - 1]
        if pd.isna(current["fast"]) or pd.isna(current["slow"]):
            continue
        crossed_up = current["fast"] > current["slow"] and (
            pd.isna(previous["slow"]) or previous["fast"] <= previous["slow"]
        )
        crossed_down = current["fast"] < current["slow"] and previous["fast"] >= previous["slow"]
        fill_row = frame.iloc[index + 1]
        if position is None and crossed_up:
            position = {
                "signal_time": frame.index[index],
                "fill_time": frame.index[index + 1],
                "fill_price": float(fill_row["open"]),
            }
        elif position is not None and crossed_down:
            position["exit_signal_time"] = frame.index[index]
            position["exit_time"] = frame.index[index + 1]
            position["exit_price"] = float(fill_row["open"])
            position["pnl"] = position["exit_price"] - position["fill_price"]
            trades.append(position)
            position = None

    if position is not None and not frame.empty:
        position["exit_signal_time"] = frame.index[-1]
        position["exit_time"] = frame.index[-1]
        position["exit_price"] = float(frame.iloc[-1]["close"])
        position["pnl"] = position["exit_price"] - position["fill_price"]
        trades.append(position)

    if not frame.empty:
        cash = float(initial_equity)
        units = 0.0
        entry_fills = {trade["fill_time"]: trade for trade in trades}
        exit_fills = {trade["exit_time"]: trade for trade in trades}
        equity_values = []
        for timestamp, row in frame.iterrows():
            entry = entry_fills.get(timestamp)
            if entry is not None and units == 0:
                units = cash / entry["fill_price"]
                cash = 0.0
            exit_trade = exit_fills.get(timestamp)
            if exit_trade is not None and units > 0:
                cash = units * exit_trade["exit_price"]
                units = 0.0
            equity_values.append(cash + units * float(row["close"]))
        equity = pd.Series(equity_values, index=frame.index, dtype=float)
    return {"trades": trades, "equity": equity, "bars": frame}


def normalize_option_quotes(payload: dict[str, Any]) -> pd.DataFrame:
    """Normalize point-in-time option bid/ask quotes."""
    rows = payload.get("quotes", []) if isinstance(payload, dict) else payload
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "bid", "ask", "mid"])
    frame = frame.rename(columns={
        "t": "timestamp", "s": "symbol", "bp": "bid", "ap": "ask",
        "bid_price": "bid", "ask_price": "ask",
    })
    required = {"timestamp", "symbol", "bid", "ask"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing option quote fields: {sorted(missing)}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["bid"] = pd.to_numeric(frame["bid"], errors="coerce")
    frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "symbol", "bid", "ask"])
    frame = frame[(frame["bid"] > 0) & (frame["ask"] >= frame["bid"])]
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2
    return frame.set_index("timestamp")["symbol bid ask mid".split()].sort_index()


def _simulate_option_arm(
    quotes: pd.DataFrame,
    signals: list[dict[str, Any]],
    strategy_type: str,
    initial_equity: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    slippage_per_share: float,
    fee_per_contract: float,
) -> dict[str, Any]:
    trades = []
    nonfills = 0
    for signal in signals:
        if signal.get("strategy_type") != strategy_type or not signal.get("actionable", True):
            continue
        signal_time = pd.Timestamp(signal["timestamp"], tz="UTC")
        rows = quotes[(quotes["symbol"] == signal["symbol"]) & (quotes.index > signal_time)]
        expiry_date = None
        if signal.get("expiry"):
            expiry_date = pd.Timestamp(signal["expiry"]).date()
            rows = rows[[timestamp.date() <= expiry_date for timestamp in rows.index]]
        if rows.empty:
            nonfills += 1
            continue
        entry_row = rows.iloc[0]
        entry = float(entry_row["ask"]) + slippage_per_share
        if entry <= 0:
            nonfills += 1
            continue
        target = entry * (1 + take_profit_pct)
        stop = entry * (1 - stop_loss_pct)
        exit_price = None
        exit_reason = "expiry" if expiry_date and rows.index[-1].date() >= expiry_date else "end_of_data"
        exit_time = rows.index[-1]
        for timestamp, row in rows.iloc[1:].iterrows():
            executable_bid = max(float(row["bid"]) - slippage_per_share, 0.01)
            if executable_bid >= target:
                exit_price, exit_reason, exit_time = executable_bid, "take_profit", timestamp
                break
            if executable_bid <= stop:
                exit_price, exit_reason, exit_time = executable_bid, "stop_loss", timestamp
                break
        if exit_price is None:
            exit_price = max(float(rows.iloc[-1]["bid"]) - slippage_per_share, 0.01)
        pnl = round((exit_price - entry) * 100 - fee_per_contract, 2)
        trades.append({
            "strategy_type": strategy_type,
            "symbol": signal["symbol"],
            "signal_time": signal_time.isoformat(),
            "entry_time": rows.index[0].isoformat(),
            "entry_price": round(entry, 2),
            "exit_time": exit_time.isoformat(),
            "exit_price": round(exit_price, 2),
            "exit_reason": exit_reason,
            "pnl": pnl,
        })
    pnls = [trade["pnl"] for trade in trades]
    equity_values = [float(initial_equity)]
    for pnl in pnls:
        equity_values.append(round(equity_values[-1] + pnl, 2))
    equity = pd.Series(equity_values, dtype=float)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [-pnl for pnl in pnls if pnl < 0]
    sample_size = len(pnls)
    return {
        "strategy_type": strategy_type,
        "trades": trades,
        "nonfilled_signals": nonfills,
        "equity": equity,
        "metrics": {
            "fill_rate": len(trades) / (len(trades) + nonfills) if trades or nonfills else 0.0,
            "filled_trades": len(trades),
            "sample_size": sample_size,
            "total_pnl": round(sum(pnls), 2),
            "win_rate": len(wins) / len(pnls) if pnls else 0.0,
            "expectancy": round(sum(pnls) / sample_size, 2) if sample_size else 0.0,
            "average_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "average_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "profit_factor": round(sum(wins) / sum(losses), 4) if losses else None,
            "max_drawdown": float((equity / equity.cummax() - 1).min()),
        },
    }


def run_options_backtest(
    quotes: pd.DataFrame,
    signals: list[dict[str, Any]],
    strategy_type: str | None = None,
    initial_equity: float = 100_000.0,
    take_profit_pct: float = 0.35,
    stop_loss_pct: float = 0.50,
    slippage_per_share: float = 0.0,
    fee_per_contract: float = 0.0,
) -> dict[str, Any]:
    """Run one or both executable single-leg strategy arms."""
    strategies = [strategy_type] if strategy_type else ["LONG_CALL", "LONG_PUT"]
    results = {
        name: _simulate_option_arm(
            quotes, signals, name, initial_equity, take_profit_pct,
            stop_loss_pct, slippage_per_share, fee_per_contract,
        )
        for name in strategies
    }
    if strategy_type:
        return results[strategy_type]
    trades = sorted(
        [trade for result in results.values() for trade in result["trades"]],
        key=lambda trade: trade["exit_time"],
    )
    pnls = [trade["pnl"] for trade in trades]
    equity = pd.Series([float(initial_equity)] + [initial_equity + sum(pnls[:i]) for i in range(1, len(pnls) + 1)])
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [-pnl for pnl in pnls if pnl < 0]
    sample_size = len(pnls)
    return {
        "strategy_type": "COMBINED",
        "trades": trades,
        "nonfilled_signals": sum(result["nonfilled_signals"] for result in results.values()),
        "equity": equity,
        "metrics": {
            "sample_size": sample_size,
            "filled_trades": sample_size,
            "total_pnl": round(sum(pnls), 2),
            "expectancy": round(sum(pnls) / sample_size, 2) if sample_size else 0.0,
            "win_rate": len(wins) / sample_size if sample_size else 0.0,
            "average_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "average_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "profit_factor": round(sum(wins) / sum(losses), 4) if losses else None,
            "max_drawdown": float((equity / equity.cummax() - 1).min()),
        },
        "by_strategy": results,
    }
