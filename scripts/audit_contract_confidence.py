"""Read-only diagnostics for option-profit shadow outcomes."""
import json
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from execution.shadow_store import (
    CALIBRATION_VERSION,
    archived_observation_count,
    pending_observations,
    resolved_outcomes,
)
from quant_engine.momentum import _wilson_lower_bound


def _bucket(row: dict) -> str:
    return "|".join(
        str(row.get(key))
        for key in ("timeframe", "direction", "volatility_regime", "dte_bucket", "delta_bucket")
    )


def main() -> None:
    resolved = resolved_outcomes()
    pending = pending_observations()
    wins = sum(bool(row["profitable"]) for row in resolved)
    resolved_groups = {}
    for row in resolved:
        resolved_groups.setdefault(_bucket(row), []).append(row)
    ready_groups = {
        key: len(rows)
        for key, rows in resolved_groups.items()
        if len(rows) >= config.CONTRACT_CONFIDENCE_MIN_SAMPLES
        and _wilson_lower_bound(sum(bool(row["profitable"]) for row in rows), len(rows))
        >= config.MOMENTUM_MIN_PROBABILITY_LB
    }
    report = {
        "audit_version": "option-profit-confidence-audit-v2",
        "calibration_version": CALIBRATION_VERSION,
        "threshold": config.MOMENTUM_MIN_PROBABILITY_LB,
        "min_samples": config.CONTRACT_CONFIDENCE_MIN_SAMPLES,
        "horizon_bars": config.CONTRACT_CONFIDENCE_HORIZON,
        "resolved_count": len(resolved),
        "pending_count": len(pending),
        "pending_by_timeframe": dict(Counter(row["timeframe"] for row in pending)),
        "archived_legacy_count": archived_observation_count(),
        "profitable_count": wins,
        "win_rate": round(wins / len(resolved), 4) if resolved else None,
        "total_net_pnl_usd": round(sum(float(row["net_pnl_usd"]) for row in resolved), 2),
        "by_direction": dict(Counter(row["direction"] for row in resolved)),
        "by_regime": dict(Counter(row["volatility_regime"] for row in resolved)),
        "resolved_by_bucket": {key: len(rows) for key, rows in resolved_groups.items()},
        "green_ready_buckets": ready_groups,
        "ready_for_green": bool(ready_groups),
        "note": "No broker state is modified; stock-history proxy and option-profit calibration are separate.",
    }
    output = config.BASE_DIR / "runs" / "contract_confidence_audit_2026-09-02.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "resolved": len(resolved), "pending": len(pending)}, indent=2))


if __name__ == "__main__":
    main()
