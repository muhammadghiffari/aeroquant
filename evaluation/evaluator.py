"""Evaluator: closes the autonomy loop.

After every cycle:
1. sync newly CLOSED trades from the ledger into SQLite
2. write a post-mortem for each into LanceDB semantic memory
3. when there is new material, an LLM evaluator distills LESSONS
   (state/lessons.json) that are injected into the chief's next prompts
4. expose stats + memory for the dashboard

Hard risk rules are NEVER touched by evaluation -- it only shapes strategy
preference, not limits.
"""
import json
import logging
from collections import Counter

import config
from agents.base_agent import BaseAgent
from evaluation import memory, store

log = logging.getLogger(__name__)


class LessonDistiller(BaseAgent):
    """LLM that turns trade history + post-mortems into reusable lessons."""

    name = "LessonDistiller"
    system_prompt = (
        "You are the self-evaluation module of an autonomous options trading system. "
        "You receive performance stats and post-mortems of closed trades. Extract up to "
        "5 SHORT, actionable lessons for the Strategy Decision Agent (one sentence each, "
        "imperative, concrete: e.g. 'avoid 1-wide credit spreads on SPY -- credit too "
        "small vs risk'). Only include lessons supported by the data. If the sample is "
        "too small or nothing notable, return an empty list. Output JSON only."
    )
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "lessons": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": ["summary", "lessons", "confidence"],
    }

    def fallback(self) -> dict:
        return {"summary": "evaluation unavailable", "lessons": []}


def _trade_context(trade: dict, results: list[dict]) -> dict:
    """Pull the market context that surrounded this trade from cycle results."""
    ctx: dict = {}
    for r in results:
        pos = r.get("reports") or {}
        prop = pos.get("proposal") or {}
        if prop and trade.get("underlying") == r.get("symbol"):
            q = r.get("quant_summary") or {}
            ctx = {
                "iv_rank": q.get("iv_rank_proxy"),
                "z_score": q.get("z_score_20d"),
                "event_risk": (pos.get("news") or {}).get("event_risk"),
                "lesson": (prop.get("rationale") or "")[:200],
            }
            break
    return ctx


def update_lessons() -> dict:
    """Distill lessons from stats + recent memory; persist to state/lessons.json."""
    stats = store.stats()
    recent = memory.recent(8)
    if stats.get("total_closed", 0) == 0:
        return {"updated": False, "reason": "no closed trades yet"}

    out = LessonDistiller().run(
        {"performance_stats": stats, "recent_postmortems": recent}
    )
    lessons = [str(x).strip()[:240] for x in out.get("lessons", []) if str(x).strip()][:5]
    payload = {"summary": out.get("summary", ""), "lessons": lessons, "degraded": out.get("degraded", False)}
    # never wipe previously learned lessons when the distiller comes back empty
    # (degraded LLM, "sample too small", etc.)
    if lessons:
        path = config.STATE_DIR / "lessons.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(lessons, f, indent=1)
    store.save_evaluation("lessons", payload)
    return {"updated": bool(lessons), "n_lessons": len(lessons), "summary": payload["summary"]}


def run_after_cycle(data: dict, results: list[dict]) -> dict:
    """Pipeline hook: evaluate everything that changed this cycle."""
    if not config.EVALUATION_ENABLED:
        return {"enabled": False}
    summary: dict = {"enabled": True}
    try:
        action_counts = dict(Counter(str(r.get("action", "UNKNOWN")) for r in results))
        summary["actions_reviewed"] = len(results)
        summary["action_counts"] = action_counts
        store.save_evaluation("cycle_actions", {
            "actions_reviewed": len(results),
            "action_counts": action_counts,
            "symbols": [str(r.get("symbol", "")) for r in results],
        })
        new_trades = store.sync_from_ledger(data)
        summary["new_closed_trades"] = len(new_trades)
        embedded = 0
        for t in new_trades:
            if memory.add_postmortem(t, _trade_context(t, results)):
                embedded += 1
        summary["postmortems_written"] = embedded
        summary["memory_rows"] = memory.count()
        if new_trades and embedded:
            summary.update(update_lessons())
        summary["stats"] = store.stats()
    except Exception as exc:  # noqa: BLE001
        log.exception("evaluation failed")
        summary["error"] = str(exc)
    return summary


def dashboard_payload() -> dict:
    """Everything the dashboard evaluation panel needs (never raises)."""
    try:
        return {
            "stats": store.stats(),
            "lessons": _read_lessons(),
            "recent_trades": store.recent_trades(8),
            "postmortems": memory.recent(5),
            "memory_rows": memory.count(),
            "last_review": store.latest_evaluation("lessons"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _read_lessons() -> list[str]:
    try:
        with open(config.STATE_DIR / "lessons.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
