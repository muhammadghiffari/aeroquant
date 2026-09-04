"""Semantic memory: post-mortems of closed trades.

Primary store: embedded LanceDB (file-based, VPS-ready, hybrid filter+vector
queries). On machines where LanceDB's pyarrow DLL is blocked (OS policy), a
zero-dependency fallback (JSONL + numpy cosine) keeps the memory working --
same API, same data shape, swap is automatic and logged once.

Embeddings: local Ollama nomic-embed-text (free, offline).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

import config

log = logging.getLogger(__name__)

_TABLE = "postmortems"
_db = None
_lance_ok: bool | None = None
_embed_ok: bool | None = None


def _db_path():
    p = config.STATE_DIR / "memory_lancedb"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _fallback_path() -> Path:
    return config.STATE_DIR / "memory_fallback.jsonl"


def _get_db():
    global _db, _lance_ok
    if _lance_ok is None:
        try:
            import lancedb  # noqa: F401

            _lance_ok = True
        except Exception as exc:  # noqa: BLE001  (pyarrow DLL blocked etc.)
            _lance_ok = False
            log.warning("lancedb unavailable (%s) -- using JSONL+numpy fallback", str(exc)[:120])
    if _db is None and _lance_ok:
        import lancedb

        _db = lancedb.connect(_db_path())
    return _db


def embeddings_available() -> bool:
    global _embed_ok
    if config.EMBED_PROVIDER != "ollama":
        _embed_ok = False
        return False
    if _embed_ok is None:
        try:
            r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
            models = [m.get("name", "") for m in r.json().get("models", [])]
            base = config.EMBED_MODEL.split(":")[0]
            _embed_ok = any(m.startswith(base) for m in models)
            if not _embed_ok:
                log.warning("embed model %s missing -- memory disabled", config.EMBED_MODEL)
        except Exception:  # noqa: BLE001
            _embed_ok = False
    return _embed_ok


def embed(text: str) -> list[float]:
    """Embed via local Ollama; retry -- first call may cold-load the model."""
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                f"{config.OLLAMA_URL}/api/embed",
                json={"model": config.EMBED_MODEL, "input": text},
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json()["embeddings"][0]
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("embed attempt %d failed: %s", attempt, exc)
    raise last  # type: ignore[misc]


def postmortem_text(trade: dict, context: dict | None = None) -> str:
    ctx = context or {}
    return (
        f"TRADE POST-MORTEM | {trade.get('underlying')} {trade.get('strategy_type')} | "
        f"entry_net={trade.get('entry_net')} realized_pl={trade.get('realized_pl')} | "
        f"exit_reason={trade.get('exit_reason')} | "
        f"market: iv_rank={ctx.get('iv_rank')} z_score={ctx.get('z_score')} "
        f"event_risk={ctx.get('event_risk')} | "
        f"lesson: {ctx.get('lesson', 'n/a')}"
    )


def add_postmortem(trade: dict, context: dict | None = None) -> bool:
    """Store one post-mortem, using vectors when available and text otherwise."""
    text = postmortem_text(trade, context)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": trade.get("underlying", ""),
        "strategy_type": trade.get("strategy_type", ""),
        "realized_pl": float(trade.get("realized_pl", 0) or 0),
        "text": text,
    }
    if embeddings_available():
        try:
            row["vector"] = embed(text)
            db = _get_db()
            if db is not None:
                try:
                    table = db.open_table(_TABLE)
                    table.add([row])
                except Exception:  # noqa: BLE001  (table missing -> create)
                    db.create_table(_TABLE, data=[row])
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("vector post-mortem unavailable (%s) -- storing text fallback", str(exc)[:120])
    with open(_fallback_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return True


def search_similar(query: str, k: int = 3, symbol: str | None = None) -> list[dict]:
    """Top-k similar past situations; optionally constrained to one symbol."""
    if not embeddings_available():
        return []
    qvec = np.asarray(embed(query), dtype=float)
    db = _get_db()
    try:
        if db is not None:
            table = db.open_table(_TABLE)
            q = table.search(qvec.tolist()).limit(k)
            if symbol:
                q = q.where(f"symbol = '{symbol}'", prefilter=True)
            rows = q.to_list()
            return [
                {"text": r.get("text"), "realized_pl": r.get("realized_pl"),
                 "strategy_type": r.get("strategy_type"), "symbol": r.get("symbol")}
                for r in rows
            ]
        rows = [r for r in _fallback_rows() if r.get("vector")]
        if symbol:
            rows = [r for r in rows if r.get("symbol") == symbol]
        if not rows:
            return []
        mat = np.asarray([r["vector"] for r in rows], dtype=float)
        sims = mat @ qvec / (np.linalg.norm(mat, axis=1) * np.linalg.norm(qvec) + 1e-9)
        top = np.argsort(sims)[::-1][:k]
        return [
            {"text": rows[i].get("text"), "realized_pl": rows[i].get("realized_pl"),
             "strategy_type": rows[i].get("strategy_type"), "symbol": rows[i].get("symbol"),
             "score": round(float(sims[i]), 3)}
            for i in top
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("memory search failed: %s", exc)
        return []


def _fallback_rows() -> list[dict]:
    path = _fallback_path()
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def recent(k: int = 5) -> list[dict]:
    db = _get_db()
    try:
        rows = []
        if db is not None:
            try:
                table = db.open_table(_TABLE)
                rows.extend(table.to_arrow().to_pylist())
            except Exception:  # noqa: BLE001  (text fallback may be active)
                pass
        rows.extend(_fallback_rows())
        rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return [
            {"ts": r.get("ts"), "text": r.get("text"), "realized_pl": r.get("realized_pl")}
            for r in rows[:k]
        ]
    except Exception:  # noqa: BLE001
        return []


def count() -> int:
    db = _get_db()
    total = 0
    try:
        if db is not None:
            try:
                total += db.open_table(_TABLE).count_rows()
            except Exception:  # noqa: BLE001  (text fallback may be active)
                pass
        return total + len(_fallback_rows())
    except Exception:  # noqa: BLE001
        return total
