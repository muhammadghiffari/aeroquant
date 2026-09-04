"""Small SQLite WAL store for durable cycle and order-intent audit records."""
import json
import sqlite3
from datetime import datetime, timezone

import config


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.OPERATIONAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cycles (
            cycle_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_intents (
            intent_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, account_id TEXT NOT NULL,
            client_order_id TEXT NOT NULL UNIQUE, proposal_json TEXT NOT NULL,
            status TEXT NOT NULL, broker_order_id TEXT, broker_status TEXT, created_at TEXT NOT NULL
        );
        """
    )
    return conn


def record_cycle(cycle_id: str, account_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cycles VALUES (?, ?, ?)",
            (cycle_id, account_id, datetime.now(timezone.utc).isoformat()),
        )


def create_order_intent(
    intent_id: str, cycle_id: str, account_id: str, client_order_id: str, proposal: dict
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO order_intents
            VALUES (?, ?, ?, ?, ?, 'INTENT_CREATED', NULL, NULL, ?)""",
            (intent_id, cycle_id, account_id, client_order_id, json.dumps(proposal, default=str),
             datetime.now(timezone.utc).isoformat()),
        )


def record_order_ack(intent_id: str, broker_order_id: str, broker_status: str) -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE order_intents SET status = 'ACKNOWLEDGED', broker_order_id = ?,
            broker_status = ? WHERE intent_id = ?""",
            (broker_order_id, broker_status, intent_id),
        )


def update_order_status(intent_id: str, status: str, broker_status: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE order_intents SET status = ?, broker_status = ? WHERE intent_id = ?",
            (status, broker_status, intent_id),
        )


def find_unresolved_intent(account_id: str, symbol: str) -> dict:
    terminal = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM order_intents WHERE account_id = ? ORDER BY created_at DESC",
            (account_id,),
        ).fetchall()
    for row in rows:
        item = dict(row)
        if item["status"] in terminal:
            continue
        try:
            proposal = json.loads(item["proposal_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if str(proposal.get("symbol", "")).upper() == symbol.upper():
            return item
    return {}


def unresolved_intents() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM order_intents WHERE status NOT IN ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED')"
        ).fetchall()
    return [dict(row) for row in rows]


def get_cycle_count() -> int:
    with _conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0])


def get_order_intent(intent_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM order_intents WHERE intent_id = ?", (intent_id,)).fetchone()
    return dict(row) if row else {}


def get_order_intent_by_client_id(client_order_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM order_intents WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
    return dict(row) if row else {}
