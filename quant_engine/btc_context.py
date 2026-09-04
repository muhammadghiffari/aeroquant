"""Deterministic BTC context and its bounded effect on equity signals."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config


def build_btc_context(bars: pd.DataFrame, max_age_seconds: int | None = None) -> dict:
    """Summarize BTC trend, return shock, and data quality without an LLM."""
    required = {"close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"missing BTC columns: {sorted(missing)}")
    frame = bars.copy().sort_index()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["close", "volume"])
    if len(frame) < 90:
        return _neutral("insufficient_bars")

    close = frame["close"]
    fifteen = close.resample("15min").last().dropna()
    if len(fifteen) < 21:
        return _neutral("insufficient_resampled_bars")
    returns = fifteen.pct_change()
    latest = frame.index[-1]
    age = None
    if isinstance(latest, pd.Timestamp):
        latest = latest.tz_localize("UTC") if latest.tzinfo is None else latest.tz_convert("UTC")
        age = max((pd.Timestamp.now(tz="UTC") - latest).total_seconds(), 0.0)
    max_age = config.BTC_CONTEXT_MAX_AGE_SECONDS if max_age_seconds is None else max_age_seconds
    quality = "stale" if age is not None and age > max_age else "fresh"

    ema_fast = float(fifteen.ewm(span=9, adjust=False).mean().iloc[-1])
    ema_slow = float(fifteen.ewm(span=21, adjust=False).mean().iloc[-1])
    ret_15m = float(returns.iloc[-1]) if pd.notna(returns.iloc[-1]) else 0.0
    ret_1h = float(fifteen.iloc[-1] / fifteen.iloc[-5] - 1.0) if len(fifteen) >= 5 else 0.0
    vol = float(returns.rolling(20, min_periods=20).std().iloc[-1] or 0.0)
    z_score = ret_15m / vol if vol > 0 else 0.0
    direction = "BULLISH" if ema_fast > ema_slow and ret_1h > 0 else (
        "BEARISH" if ema_fast < ema_slow and ret_1h < 0 else "NEUTRAL"
    )
    extreme = (
        quality == "fresh"
        and abs(z_score) >= config.BTC_EXTREME_ZSCORE
        and abs(ret_1h) * 100 >= config.BTC_EXTREME_RETURN_1H_PCT
    )
    regime = f"EXTREME_{direction}" if extreme and direction != "NEUTRAL" else direction
    return {
        "symbol": config.BTC_SYMBOL,
        "direction": direction,
        "regime": regime,
        "return_15m_pct": round(ret_15m * 100, 4),
        "return_1h_pct": round(ret_1h * 100, 4),
        "volatility_15m_pct": round(vol * 100, 4),
        "z_score_15m": round(z_score, 4),
        "ema_fast": round(ema_fast, 4),
        "ema_slow": round(ema_slow, 4),
        "data_age_seconds": round(age, 1) if age is not None else None,
        "data_quality": quality,
    }


def apply_btc_context(signal: dict, context: dict) -> dict:
    """Apply BTC only as secondary context, confidence, and sizing evidence."""
    if not signal.get("actionable"):
        return dict(signal)
    result = dict(signal)
    result["reasons"] = list(signal.get("reasons", []))
    result["btc_override"] = False
    result["btc_extreme_context"] = False
    result["sizing_multiplier"] = 1.0
    result["btc_alignment"] = "neutral"
    result["confidence"] = _clip(float(signal.get("confidence", 0.6)))
    result["btc_context"] = context
    btc_direction = context.get("direction")
    quality = context.get("data_quality")
    if quality != "fresh" or btc_direction not in {"BULLISH", "BEARISH"}:
        if quality != "fresh":
            result["reasons"].append(
                "btc_data_stale" if quality == "stale" else "btc_data_unavailable"
            )
        return result

    signal_direction = signal.get("direction")
    aligned = signal_direction == btc_direction
    result["btc_alignment"] = "supportive" if aligned else "conflicting"
    regime = context.get("regime", btc_direction)
    if regime in {"EXTREME_BULLISH", "EXTREME_BEARISH"}:
        result["btc_extreme_context"] = True
        result["sizing_multiplier"] = config.BTC_EXTREME_SIZE_MULTIPLIER
        result["confidence"] = _clip(float(signal.get("confidence", 0.6)) - (0.05 if not aligned else 0.0))
        result["reasons"].append("btc_extreme_move_context")
        return result

    if not aligned:
        result["sizing_multiplier"] = config.BTC_CONFLICT_SIZE_MULTIPLIER
        result["confidence"] = _clip(float(signal.get("confidence", 0.6)) - 0.15)
        result["reasons"].append("btc_direction_conflict")
    else:
        result["confidence"] = _clip(float(signal.get("confidence", 0.6)) + 0.05)
        result["reasons"].append("btc_direction_supportive")
    return result


def _neutral(reason: str) -> dict:
    return {
        "symbol": config.BTC_SYMBOL,
        "direction": "NEUTRAL",
        "regime": "NEUTRAL",
        "return_15m_pct": 0.0,
        "return_1h_pct": 0.0,
        "volatility_15m_pct": 0.0,
        "z_score_15m": 0.0,
        "data_age_seconds": None,
        "data_quality": reason,
    }


def _clip(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4) if math.isfinite(value) else 0.0
