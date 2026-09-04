"""Durable, broker-read-only option shadow observations."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from quant_engine.contract_confidence import delta_bucket, dte_bucket

LEGACY_FIXED_BAR_COUNTS = {277, 400}
DAILY_TIMEFRAME = "1D"
HOURLY_TIMEFRAME = "1H"
CALIBRATION_VERSION = "hourly-1bar-history-v2"


def bar_count_from_timestamp(timestamp, timeframe: str = DAILY_TIMEFRAME) -> int:
    """Map a market-bar timestamp to a monotonic daily or hourly ordinal."""
    timeframe = str(timeframe).upper()
    if timeframe == HOURLY_TIMEFRAME:
        if isinstance(timestamp, datetime):
            value = timestamp
        else:
            value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        local = value.astimezone(ZoneInfo("America/New_York"))
        day = bar_count_from_timestamp(local.date(), DAILY_TIMEFRAME)
        return day * 7 + max(0, min(local.hour - 9, 6))
    if isinstance(timestamp, datetime):
        market_date = timestamp.date()
    elif isinstance(timestamp, date):
        market_date = timestamp
    else:
        value = str(timestamp).replace("Z", "+00:00")
        try:
            market_date = datetime.fromisoformat(value).date()
        except ValueError:
            market_date = date.fromisoformat(str(timestamp)[:10])

    epoch = date(1970, 1, 1)
    days = (market_date - epoch).days
    full_weeks, remainder = divmod(days, 7)
    ordinal = full_weeks * 5
    for offset in range(remainder):
        if (epoch + timedelta(days=full_weeks * 7 + offset)).weekday() < 5:
            ordinal += 1
    return ordinal


def _stored_bar_count(row) -> int:
    """Read new ordinals and translate rows created by the fixed-length bug."""
    stored = int(row["entry_bar_count"])
    if row["timeframe"] == DAILY_TIMEFRAME and stored in LEGACY_FIXED_BAR_COUNTS:
        return bar_count_from_timestamp(row["entry_timestamp"], DAILY_TIMEFRAME)
    return stored


def _migrate_legacy_bar_counts(conn: sqlite3.Connection) -> None:
    """Repair observations created before bar_count became date-based."""
    rows = conn.execute(
        "SELECT observation_id, entry_bar_count, entry_timestamp "
        "FROM shadow_observations WHERE timeframe = ? AND entry_bar_count IN (?, ?)",
        (DAILY_TIMEFRAME, *sorted(LEGACY_FIXED_BAR_COUNTS)),
    ).fetchall()
    for row in rows:
        try:
            conn.execute(
                "UPDATE shadow_observations SET entry_bar_count = ? WHERE observation_id = ?",
                (bar_count_from_timestamp(row["entry_timestamp"], DAILY_TIMEFRAME), row["observation_id"]),
            )
        except sqlite3.IntegrityError:
            # Resolver compatibility below still handles a rare unique-key collision.
            continue


def _connection() -> sqlite3.Connection:
    config.OPERATIONAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.OPERATIONAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_observations (
            observation_id TEXT PRIMARY KEY,
            underlying TEXT NOT NULL,
            contract_symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            volatility_regime TEXT,
            dte INTEGER,
            delta REAL,
            dte_bucket TEXT,
            delta_bucket TEXT,
             entry_bar_count INTEGER NOT NULL,
             timeframe TEXT NOT NULL DEFAULT '1D',
             calibration_version TEXT NOT NULL DEFAULT 'hourly-1bar-history-v2',
             entry_timestamp TEXT NOT NULL,
            entry_ask REAL NOT NULL,
            entry_bid REAL,
            horizon_bars INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            exit_bid REAL,
            exit_timestamp TEXT,
            net_pnl_usd REAL,
            profitable INTEGER,
            UNIQUE(contract_symbol, entry_bar_count)
        )"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(shadow_observations)")}
    if "timeframe" not in columns:
        conn.execute(
            "ALTER TABLE shadow_observations ADD COLUMN timeframe TEXT NOT NULL DEFAULT '1D'"
        )
    if "calibration_version" not in columns:
        conn.execute(
            "ALTER TABLE shadow_observations ADD COLUMN calibration_version TEXT NOT NULL DEFAULT 'legacy-v1'"
        )
    conn.execute(
        "UPDATE shadow_observations SET status = 'ARCHIVED' "
        "WHERE calibration_version != ? AND status != 'ARCHIVED'",
        (CALIBRATION_VERSION,),
    )
    _migrate_legacy_bar_counts(conn)
    return conn


def record_observation(observation: dict) -> bool:
    """Insert one observation; repeated contract/bar snapshots are ignored."""
    with _connection() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO shadow_observations
            (observation_id, underlying, contract_symbol, direction,
             volatility_regime, dte, delta, dte_bucket, delta_bucket,
            entry_bar_count, timeframe, calibration_version, entry_timestamp,
            entry_ask, entry_bid, horizon_bars)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation["observation_id"],
                observation["underlying"],
                observation["contract_symbol"],
                str(observation["direction"]).upper(),
                observation.get("volatility_regime"),
                int(observation.get("dte", 0)),
                float(observation.get("delta", 0.0)),
                dte_bucket(observation.get("dte", 0)),
                delta_bucket(observation.get("delta", 0.0)),
                int(observation["entry_bar_count"]),
                str(observation.get("timeframe", DAILY_TIMEFRAME)).upper(),
                CALIBRATION_VERSION,
                observation["entry_timestamp"],
                float(observation["entry_ask"]),
                float(observation.get("entry_bid", 0.0) or 0.0),
                int(observation.get("horizon_bars", config.CONTRACT_CONFIDENCE_HORIZON)),
            ),
        )
        return cur.rowcount == 1


def pending_observations(underlying: str | None = None, timeframe: str | None = None) -> list[dict]:
    with _connection() as conn:
        clauses, values = ["status = 'PENDING'", "calibration_version = ?"], [CALIBRATION_VERSION]
        if underlying:
            clauses.append("underlying = ?")
            values.append(underlying.upper())
        if timeframe:
            clauses.append("timeframe = ?")
            values.append(str(timeframe).upper())
        rows = conn.execute(
            f"SELECT * FROM shadow_observations WHERE {' AND '.join(clauses)}", values
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_observations(
    underlying: str,
    *,
    current_bar_count: int,
    current_timestamp: str | None = None,
    quotes: dict[str, dict] | None = None,
    timeframe: str = DAILY_TIMEFRAME,
) -> int:
    """Resolve matured observations using the later executable bid."""
    quotes = quotes or {}
    resolved = 0
    stamp = current_timestamp or datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_observations WHERE status = 'PENDING' "
            "AND calibration_version = ? AND underlying = ? AND timeframe = ?",
            (CALIBRATION_VERSION, underlying.upper(), str(timeframe).upper()),
        ).fetchall()
        for row in rows:
            if current_bar_count - _stored_bar_count(row) < int(row["horizon_bars"]):
                continue
            quote = quotes.get(row["contract_symbol"]) or {}
            bid = float(quote.get("bid", 0) or 0)
            if bid <= 0:
                continue
            pnl = round((bid - float(row["entry_ask"])) * 100.0, 2)
            conn.execute(
                """UPDATE shadow_observations SET status = 'RESOLVED', exit_bid = ?,
                exit_timestamp = ?, net_pnl_usd = ?, profitable = ?
                WHERE observation_id = ?""",
                (bid, stamp, pnl, int(pnl > 0), row["observation_id"]),
            )
            resolved += 1
    return resolved


def process_snapshot(underlying: str, context: dict, candidates: list[dict]) -> dict:
    """Resolve matured rows and record fresh, valid shadow candidates."""
    bar_count = int(context.get("bar_count", 0) or 0)
    timeframe = str(context.get("timeframe", DAILY_TIMEFRAME)).upper()
    if bar_count <= 0:
        return {"resolved": 0, "recorded": 0}
    resolved = resolve_observations(
        underlying,
        current_bar_count=bar_count,
        current_timestamp=context.get("bar_timestamp"),
        quotes=context.get("pending_quotes") or {},
        timeframe=timeframe,
    )
    recorded = 0
    for candidate in candidates or []:
        profitability = candidate.get("profitability") or {}
        if not profitability.get("valid"):
            continue
        symbol = candidate["symbol"]
        observation = {
            "observation_id": f"{underlying.upper()}:{timeframe}:{symbol}:{bar_count}",
            "underlying": underlying.upper(),
            "contract_symbol": symbol,
            "direction": candidate["direction"],
            "volatility_regime": (candidate.get("contract_confidence") or {}).get(
                "volatility_regime"
            ),
            "dte": candidate.get("dte", 0),
            "delta": candidate.get("delta", 0.0),
            "entry_bar_count": bar_count,
            "timeframe": timeframe,
            "entry_timestamp": context.get("bar_timestamp") or datetime.now(timezone.utc).isoformat(),
            "entry_ask": candidate.get("ask", 0),
            "entry_bid": candidate.get("bid", 0),
            "horizon_bars": config.CONTRACT_CONFIDENCE_HORIZON,
        }
        recorded += int(record_observation(observation))
    return {"resolved": resolved, "recorded": recorded}


def resolved_outcomes(underlying: str | None = None, timeframe: str | None = None) -> list[dict]:
    with _connection() as conn:
        clauses, values = ["status = 'RESOLVED'", "calibration_version = ?"], [CALIBRATION_VERSION]
        if underlying:
            clauses.append("underlying = ?")
            values.append(underlying.upper())
        if timeframe:
            clauses.append("timeframe = ?")
            values.append(str(timeframe).upper())
        rows = conn.execute(
            f"SELECT * FROM shadow_observations WHERE {' AND '.join(clauses)}", values
        ).fetchall()
    outcomes = []
    for row in rows:
        item = dict(row)
        item["profitable"] = bool(item["profitable"])
        outcomes.append(item)
    return outcomes


def archived_observation_count() -> int:
    with _connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM shadow_observations WHERE status = 'ARCHIVED'"
        ).fetchone()
    return int(row[0])
