"""SQLite evaluation store: deterministic performance numbers.

File-based (state/evaluation.db), zero-server, VPS-portable. Synced from the
JSON ledger after every cycle; the chief gets these stats injected into its
prompt so strategy selection is informed by real results.
"""
import json
import sqlite3

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id            TEXT PRIMARY KEY,
    underlying    TEXT,
    strategy_type TEXT,
    qty           INTEGER,
    opened_at     TEXT,
    closed_at     TEXT,
    entry_net     REAL,
    realized_pl   REAL,
    exit_reason   TEXT,
    max_loss_usd  REAL
);
CREATE TABLE IF NOT EXISTS evaluations (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT,
    kind    TEXT,
    payload TEXT
);
"""


def _conn() -> sqlite3.Connection:
    config.STATE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(config.STATE_DIR / "evaluation.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def sync_from_ledger(data: dict) -> list[dict]:
    """Upsert CLOSED ledger positions; return the newly-closed ones."""
    init_db()
    new: list[dict] = []
    with _conn() as conn:
        for p in data.get("positions", []):
            if p.get("status") != "CLOSED":
                continue
            cur = conn.execute("SELECT id FROM trades WHERE id = ?", (p["id"],))
            if cur.fetchone():
                continue
            conn.execute(
                """INSERT INTO trades (id, underlying, strategy_type, qty, opened_at,
                   closed_at, entry_net, realized_pl, exit_reason, max_loss_usd)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    p["id"], p.get("underlying"), p.get("strategy_type"),
                    int(p.get("qty", 1)), p.get("opened_at"), p.get("closed_at"),
                    float(p.get("net_credit_or_debit_per_unit", 0) or 0),
                    float(p.get("realized_pl", 0) or 0),
                    p.get("exit_reason"), float(p.get("max_loss_usd", 0) or 0),
                ),
            )
            new.append(p)
    return new


def stats() -> dict:
    """Win rate / P/L overall and per strategy type (hard numbers for prompts)."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT strategy_type, COUNT(*) n, SUM(realized_pl) total_pl, "
            "AVG(realized_pl) avg_pl, "
            "SUM(CASE WHEN realized_pl > 0 THEN 1 ELSE 0 END) wins "
            "FROM trades GROUP BY strategy_type"
        ).fetchall()
        tot = conn.execute(
            "SELECT COUNT(*) n, SUM(realized_pl) total_pl, "
            "SUM(CASE WHEN realized_pl > 0 THEN 1 ELSE 0 END) wins FROM trades"
        ).fetchone()

    by_strategy = {
        r["strategy_type"]: {
            "n": r["n"],
            "win_rate": round(r["wins"] / r["n"], 2) if r["n"] else None,
            "total_pl": round(r["total_pl"] or 0, 2),
            "avg_pl": round(r["avg_pl"] or 0, 2),
        }
        for r in rows
    }
    n = tot["n"] or 0
    return {
        "total_closed": n,
        "win_rate": round(tot["wins"] / n, 2) if n else None,
        "total_pl": round(tot["total_pl"] or 0, 2),
        "by_strategy": by_strategy,
    }


def recent_trades(k: int = 10) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (k,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_evaluation(kind: str, payload: dict) -> None:
    from datetime import datetime, timezone

    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO evaluations (ts, kind, payload) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), kind, json.dumps(payload, default=str)),
        )


def latest_evaluation(kind: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT payload FROM evaluations WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None
