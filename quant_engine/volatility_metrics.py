"""Volatility metrics: HV, IV rank proxy, HV/IV spread.

NOTE on IV Rank: Alpaca option history starts ~2024 and provides no historical
ATM-IV series. Per spec (AGENTS.md section 4), we use an HV-based proxy:
  iv_rank_proxy = percentile rank of current HV(20d) within trailing ~1y of
                  rolling HV(20d), scaled 0-100.
It is labeled `method: hv_proxy` everywhere so downstream consumers know.
"""
import math

import numpy as np


def log_returns(closes):
    arr = np.asarray(closes, dtype=float)
    return np.diff(np.log(arr))


def realized_vol(closes: list[float], window: int = 30) -> float:
    """Annualized realized vol over the last `window` returns."""
    r = log_returns(closes)[-window:]
    if len(r) < 5:
        return float("nan")
    return float(np.std(r, ddof=1) * math.sqrt(252))


def hv_series(closes: list[float], window: int = 20) -> np.ndarray:
    """Rolling annualized HV series (one value per bar after warmup)."""
    r = log_returns(closes)
    if len(r) < window:
        return np.array([])
    out = [
        np.std(r[i - window + 1 : i + 1], ddof=1) * math.sqrt(252)
        for i in range(window - 1, len(r))
    ]
    return np.array(out)


def percentile_rank(value: float, series) -> float | None:
    s = np.asarray([x for x in series if x == x], dtype=float)  # drop NaN
    if len(s) < 30 or value != value:
        return None
    return float((s < value).mean() * 100.0)


def volatility_metrics(closes: list[float], current_atm_iv: float | None) -> dict:
    hv30 = realized_vol(closes, 30)
    series = hv_series(closes, 20)
    iv_rank_proxy = percentile_rank(hv30, series)

    out = {
        "hv_20d": round(realized_vol(closes, 20), 4),
        "hv_30d_realized": round(hv30, 4) if hv30 == hv30 else None,
        "hv_percentile_1y": round(iv_rank_proxy, 1) if iv_rank_proxy is not None else None,
        "iv_rank_proxy_hv_based": round(iv_rank_proxy, 1) if iv_rank_proxy is not None else None,
        "iv_rank_method": "hv_proxy",
    }
    if current_atm_iv and hv30 == hv30:
        out["current_iv"] = round(current_atm_iv, 4)
        out["hv_iv_spread"] = round(current_atm_iv - hv30, 4)
    return out
