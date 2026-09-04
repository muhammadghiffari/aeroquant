"""Simple JSON ledger: tracked strategy positions + daily stats.

Single-writer (one pipeline process) so no locking beyond atomic rename.
"""
import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np

import config

log = logging.getLogger(__name__)

_EMPTY = {"positions": [], "daily": {}}


@contextmanager
def ledger_transaction():
    """Serialize a complete ledger load/mutate/save transaction across processes."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = config.STATE_DIR / "ledger.lock"
    handle = open(lock_path, "a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _today() -> str:
    return config.market_date().isoformat()


def load() -> dict:
    if not config.LEDGER_PATH.exists():
        return json.loads(json.dumps(_EMPTY))
    try:
        with open(config.LEDGER_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        log.error("ledger corrupt -- starting fresh backup at ledger.corrupt.json")
        config.LEDGER_PATH.replace(config.STATE_DIR / "ledger.corrupt.json")
        return json.loads(json.dumps(_EMPTY))
    data.setdefault("positions", [])
    data.setdefault("daily", {})
    return data


def _json_safe(value):
    """Convert analytics scalars to JSON without losing Decimal precision."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def save(data: dict) -> None:
    tmp = config.LEDGER_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_json_safe(data), f, indent=1, allow_nan=False)
    tmp.replace(config.LEDGER_PATH)


def daily_stats(data: dict) -> dict:
    return data["daily"].setdefault(
        _today(), {"realized_pl": 0.0, "rejected_streak": 0}
    )


def add_position(data: dict, position: dict) -> None:
    data["positions"].append(position)


def mark_closed(data: dict, position_id: str, exit_reason: str,
                realized_pl: float) -> None:
    for p in data["positions"]:
        if p["id"] == position_id and p["status"] in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}:
            p["status"] = "CLOSED"
            p["closed_at"] = datetime.now(timezone.utc).isoformat()
            p["exit_reason"] = exit_reason
            p["realized_pl"] = round(realized_pl, 2)
            daily_stats(data)["realized_pl"] += realized_pl
            return
    log.warning("mark_closed: position %s not found/open", position_id)


def mark_recovery_required(data: dict, position_id: str, reason: str) -> None:
    """Block new entries until a broker position mismatch is resolved."""
    for p in data["positions"]:
        if p["id"] == position_id and p["status"] in {"OPEN", "RECOVERY_REQUIRED"}:
            p["status"] = "RECOVERY_REQUIRED"
            p["recovery_reason"] = reason
            p["recovery_at"] = datetime.now(timezone.utc).isoformat()
            return
    log.warning("mark_recovery_required: position %s not found/open", position_id)


def bump_rejected_streak(data: dict) -> int:
    st = daily_stats(data)
    st["rejected_streak"] += 1
    return st["rejected_streak"]


def reset_rejected_streak(data: dict) -> None:
    daily_stats(data)["rejected_streak"] = 0


def kill_switch_active(data: dict, equity: float) -> tuple[bool, str]:
    st = daily_stats(data)
    if st["rejected_streak"] >= config.DAILY_MAX_REJECTED_IN_ROW:
        return True, f"{st['rejected_streak']} consecutive rejections today"
    if equity > 0 and st["realized_pl"] <= -equity * config.DAILY_MAX_LOSS_PCT:
        return True, f"daily realized loss ${st['realized_pl']:.0f} exceeds limit"
    return False, ""
