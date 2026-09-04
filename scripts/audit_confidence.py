"""Read-only audit comparing raw and conditioned momentum decisions."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from data_engine.stock_data import get_daily_bars
from quant_engine.confidence import build_confidence_signal
from quant_engine.momentum import build_momentum_signal


def _no_lookahead_check(closes: np.ndarray, endpoint: int) -> bool:
    original = build_confidence_signal({"close": closes}, as_of_index=endpoint)
    changed = closes.copy()
    changed[endpoint + 1 :] *= 4.0
    return original == build_confidence_signal({"close": changed}, as_of_index=endpoint)


def audit_symbol(symbol: str) -> dict:
    bars = get_daily_bars(symbol, days=400)
    closes = bars["close"].to_numpy(dtype=float)
    raw = build_momentum_signal(bars)
    confidence = build_confidence_signal(bars)
    endpoints = range(61, max(61, len(closes) - 5), max(1, len(closes) // 8))
    no_lookahead = all(_no_lookahead_check(closes, endpoint) for endpoint in endpoints)
    return {
        "symbol": symbol,
        "bar_count": len(closes),
        "first_bar": str(bars.index[0]),
        "last_bar": str(bars.index[-1]),
        "raw": {
            "direction": raw.get("directional_bias", raw.get("direction")),
            "probability": raw.get("probability"),
            "lower_bound": raw.get("probability_lower_bound"),
            "actionable": raw.get("actionable"),
            "reasons": raw.get("reasons", []),
        },
        "confidence": confidence,
        "no_lookahead": no_lookahead,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=config.BASE_DIR / "runs" / "confidence_audit_2026-09-01.json")
    args = parser.parse_args()
    symbols = list(config.WATCHLIST_SYMBOLS)
    report = {
        "audit_version": "confidence-audit-v1",
        "threshold": config.MOMENTUM_MIN_PROBABILITY_LB,
        "min_samples": config.MOMENTUM_MIN_SAMPLES,
        "symbols": [audit_symbol(symbol) for symbol in symbols],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "symbols": symbols}, indent=2))


if __name__ == "__main__":
    main()
