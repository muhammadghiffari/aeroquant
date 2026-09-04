"""Underlying trend z-score (numeric only -- labeling is the LLM's job)."""
import numpy as np


def trend_metrics(closes: list[float]) -> dict:
    arr = np.asarray(closes, dtype=float)
    price = float(arr[-1])
    sma20 = float(arr[-20:].mean()) if len(arr) >= 20 else None
    sma50 = float(arr[-50:].mean()) if len(arr) >= 50 else None
    win20 = arr[-21:]
    std20 = float(np.std(win20, ddof=1)) if len(win20) >= 10 else None

    z = None
    if sma20 and std20 and std20 > 0:
        z = round((price - sma20) / std20, 3)

    mom20 = (
        round((price / float(arr[-21]) - 1) * 100, 2) if len(arr) >= 21 else None
    )
    return {
        "price": round(price, 2),
        "sma_20": round(sma20, 2) if sma20 else None,
        "sma_50": round(sma50, 2) if sma50 else None,
        "z_score_20d": z,
        "momentum_20d_pct": mom20,
    }
