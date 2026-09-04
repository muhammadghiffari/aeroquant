"""Optional Telegram alert delivery with fail-closed credential handling."""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
_TELEGRAM_SAFE_LIMIT = 3900
TELEGRAM_OUTBOX_MAX_ATTEMPTS = 5
_STAGE_DETAIL_KEYS = {
    "action", "active_positions", "calls", "candidate_id", "decision", "direction",
    "entry_actionable", "error", "exits", "mode", "order_id", "order_status",
    "reason", "status", "symbols", "duration_s",
}
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret[_-]?key|bot[_-]?token|authorization|bearer)\s*[:=]\s*[^\s,;]+"
)


def _dedupe_path() -> Path:
    return config.STATE_DIR / "telegram_alerts.json"


def _outbox_path() -> Path:
    return config.STATE_DIR / "telegram_outbox.jsonl"


def _read_sent() -> set[str]:
    try:
        with open(_dedupe_path(), encoding="utf-8") as f:
            values = json.load(f)
        return {str(value) for value in values}
    except (OSError, json.JSONDecodeError, TypeError):
        return set()


def _record_sent(event_id: str) -> None:
    path = _dedupe_path()
    path.parent.mkdir(exist_ok=True)
    sent = _read_sent()
    sent.add(event_id)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(sent)[-2000:], f, indent=1)
    tmp.replace(path)


def send_alert(event: str, message: str, event_id: str | None = None) -> dict[str, object]:
    """Send one alert when Telegram is configured; never raise into trading flow."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return {"sent": False, "reason": "not_configured"}
    event_id = str(event_id or f"{event}:{message}")
    if event_id in _read_sent():
        return {"sent": False, "reason": "duplicate", "event_id": event_id}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"[{event}] {message}"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        log.exception("telegram alert failed for event %s", event)
        return {"sent": False, "reason": "request_failed"}
    try:
        _record_sent(event_id)
    except OSError:
        log.exception("telegram alert dedupe record failed for event %s", event)
    return {"sent": True, "event_id": event_id}


def telegram_health_check() -> tuple[bool, str]:
    """Verify the configured bot and chat without exposing credentials."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False, "telegram credentials are not configured"

    base_url = f"https://api.telegram.org/bot{token}"
    checks = (("getMe", {}), ("getChat", {"chat_id": chat_id}))
    for method, params in checks:
        try:
            response = requests.get(f"{base_url}/{method}", params=params, timeout=5)
            response.raise_for_status()
            if response.json().get("ok") is not True:
                return False, f"telegram {method} returned not-ok"
        except (requests.RequestException, ValueError, AttributeError):
            log.exception("telegram health check failed for %s", method)
            return False, f"telegram {method} failed"
    return True, "ok"


def _safe_stage_text(value: object, limit: int = 240) -> str:
    text = _SECRET_VALUE_RE.sub("<redacted>", str(value))
    return text[:limit]


def _stage_message(cycle_id: str, symbol: str, stage: str, details: dict | None) -> str:
    parts = [f"cycle={_safe_stage_text(cycle_id, 80)}", f"symbol={_safe_stage_text(symbol, 32)}"]
    for key in sorted(_STAGE_DETAIL_KEYS):
        if not details or key not in details:
            continue
        value = details[key]
        if isinstance(value, (dict, list, tuple, set)):
            continue
        parts.append(f"{key}={_safe_stage_text(value)}")
    return f"{stage} " + " ".join(parts)


def _read_outbox() -> list[dict[str, object]]:
    try:
        with open(_outbox_path(), encoding="utf-8") as stream:
            events = []
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event_id") and event.get("message"):
                    events.append(event)
            return events
    except OSError:
        return []


def _write_outbox(events: list[dict[str, object]]) -> None:
    path = _outbox_path()
    if not events:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
    tmp.replace(path)


def flush_telegram_outbox(max_events: int = 25) -> list[dict[str, object]]:
    """Deliver a bounded batch; failed events remain durable for a later flush."""
    events = _read_outbox()
    if not events:
        return []

    pending: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    limit = max(0, int(max_events))
    for index, event in enumerate(events):
        if index >= limit:
            pending.append(event)
            continue
        event_id = str(event["event_id"])
        attempts = int(event.get("attempts", 0) or 0)
        if attempts >= TELEGRAM_OUTBOX_MAX_ATTEMPTS:
            result = {"sent": False, "reason": "retry_limit", "event_id": event_id}
            pending.append(event)
            results.append(result)
            continue
        result = send_alert(
            str(event.get("event", "pipeline_stage")),
            str(event["message"]),
            event_id=event_id,
        )
        result.setdefault("event_id", event_id)
        results.append(result)
        if result.get("sent") or result.get("reason") == "duplicate":
            continue
        event["attempts"] = min(attempts + 1, TELEGRAM_OUTBOX_MAX_ATTEMPTS)
        event["last_error"] = str(result.get("reason", "delivery_failed"))
        pending.append(event)

    _write_outbox(pending)
    return results


def emit_stage(
    cycle_id: str,
    symbol: str,
    stage: str,
    sequence: int,
    details: dict | None = None,
) -> dict[str, object]:
    """Persist one secret-free stage event before attempting Telegram delivery."""
    event_id = f"{cycle_id}:{symbol}:{stage}:{sequence}"
    event = {
        "event": "pipeline_stage",
        "event_id": event_id,
        "message": _stage_message(cycle_id, symbol, stage, details),
        "attempts": 0,
    }
    existing = _read_outbox()
    if not any(str(item.get("event_id")) == event_id for item in existing):
        _outbox_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_outbox_path(), "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
    results = flush_telegram_outbox()
    for result in results:
        if result.get("event_id") == event_id:
            return result
    return {"sent": False, "reason": "queued", "event_id": event_id}


def send_daily_summary(data: dict, summary_date: date | None = None) -> dict[str, object]:
    """Send one idempotent broker-confirmed daily P/L summary."""
    day = (summary_date or config.market_date()).isoformat()
    stats = (data.get("daily") or {}).get(day, {})
    closed = sum(
        1 for position in data.get("positions", [])
        if position.get("status") == "CLOSED"
        and str(position.get("closed_at", "")).startswith(day)
    )
    open_positions = sum(
        1 for position in data.get("positions", [])
        if position.get("status") in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}
    )
    message = (
        f"date={day} realized_pl=${float(stats.get('realized_pl', 0) or 0):.2f} "
        f"closed={closed} open={open_positions}"
    )
    return send_alert("daily_market_close", message, event_id=f"daily-close:{day}")


def _chunks(message: str) -> list[str]:
    return [
        message[start : start + _TELEGRAM_SAFE_LIMIT]
        for start in range(0, max(len(message), 1), _TELEGRAM_SAFE_LIMIT)
    ]


def _confidence_line(label: str, confidence: dict) -> str:
    if not isinstance(confidence, dict) or not confidence:
        return f"{label}: unavailable"
    return (
        f"{label}: state={confidence.get('state', '?')} "
        f"source={confidence.get('source', '?')} direction={confidence.get('direction', '?')} "
        f"p={confidence.get('probability', '?')} lb={confidence.get('lower_bound', '?')} "
        f"samples={confidence.get('sample_size', '?')}"
    )


def _quote_lines(candidates: list[dict]) -> list[str]:
    lines = []
    for candidate in candidates[:3]:
        activity = (
            candidate.get("live_quote_activity")
            or (candidate.get("profitability") or {}).get("activity")
            or {}
        )
        lines.append(
            "  "
            f"{candidate.get('symbol', '?')} bid={candidate.get('bid', '?')} "
            f"ask={candidate.get('ask', '?')} bid_size={activity.get('bid_size', candidate.get('bid_size', '?'))} "
            f"ask_size={activity.get('ask_size', candidate.get('ask_size', '?'))} "
            f"flow={activity.get('dominant_side', 'UNKNOWN')}"
        )
    return lines or ["  no live option candidate"]


def _cycle_flow_message(results: list[dict], data: dict, cycle: dict, exits: list[dict]) -> str:
    account = cycle.get("account") or {}
    clock = (cycle.get("mcp_context") or {}).get("clock") or {}
    active_positions = sum(
        position.get("status") in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}
        for position in (data.get("positions") or [])
    )
    lines = [
        f"CYCLE FLOW {cycle.get('cycle_id', '?')} | {cycle.get('timestamp', '?')}",
        f"MARKET/DATA: open={clock.get('is_open', '?')} symbols={','.join(cycle.get('symbols') or [])}",
        f"ACCOUNT: equity=${float(account.get('equity', 0) or 0):.2f} buying_power=${float(account.get('buying_power', 0) or 0):.2f}",
        f"POSITION MANAGEMENT: active={active_positions} exits={len(exits)}",
        f"LLM RUNTIME: available={cycle.get('llm_available', '?')} calls={(cycle.get('llm_usage') or {}).get('calls', 0)}",
    ]
    if cycle.get("skipped"):
        lines.extend([
            f"QUANT: skipped ({cycle['skipped']})",
            "SHADOW: not run",
            "LIVE QUOTE: not fetched",
            "LLM: not run",
            "STRATEGY: not run",
            "RISK: not run",
            "EXECUTION: not run",
        ])
        return "\n".join(lines)
    if cycle.get("blocked"):
        lines.extend([
            f"QUANT: blocked ({cycle['blocked']})",
            f"SHADOW: not run",
            f"LIVE QUOTE: not fetched",
            f"LLM: not run",
            f"STRATEGY: not run",
            f"RISK: not run",
            f"EXECUTION: blocked; errors={'; '.join(cycle.get('runtime_errors') or [])}",
        ])
        return "\n".join(lines)

    for result in results:
        symbol = result.get("symbol", "?")
        quant = result.get("quant_gate") or {}
        underlying = quant.get("underlying_confidence") or quant.get("confidence") or {}
        entry = quant.get("entry_confidence") or quant.get("confidence") or {}
        contract = quant.get("contract_confidence") or {}
        candidates = quant.get("candidates") or quant.get("shadow_candidates") or []
        reports = result.get("reports") or {}
        lines.extend([
            "",
            f"=== {symbol} ===",
            f"MARKET/DATA: chain={quant.get('data_quality', {}).get('n_tradable_contracts', '?')} "
            f"timeframe={quant.get('data_quality', {}).get('entry_analysis_timeframe', '1H')}",
            f"QUANT: direction={quant.get('direction', '?')} bias={quant.get('directional_bias', '?')} "
            f"entry_actionable={quant.get('entry_actionable', False)} mode={quant.get('entry_mode', 'NONE')}",
            _confidence_line("  ENTRY CONFIDENCE", entry),
            _confidence_line("  CONTRACT CONFIDENCE", contract),
            f"SHADOW: recorded={(result.get('shadow') or {}).get('recorded', 0)} "
            f"resolved={(result.get('shadow') or {}).get('resolved', 0)} "
            f"candidates={len(quant.get('shadow_candidates') or [])}",
            "LIVE QUOTE: current bid/ask and size imbalance",
            *_quote_lines(candidates),
            "LLM: " + (
                "; ".join(
                    f"{name}={'DEGRADED' if report.get('degraded') else 'OK'} "
                    f"calls={(report.get('_usage') or {}).get('calls', 0)}"
                    for name, report in reports.items()
                    if name not in {"quant_gate", "candidate_whitelist"} and isinstance(report, dict)
                ) or "not run"
            ),
        ])
        proposal = reports.get("proposal") or {}
        lines.extend([
            f"STRATEGY: type={proposal.get('strategy_type', '?')} candidate={proposal.get('candidate_id', '?')}",
            f"  rationale={str(proposal.get('rationale', result.get('rejection_reason', 'none')))[:900]}",
        ])
        risk = reports.get("risk_decision") or {}
        checks = risk.get("checks") or {}
        lines.extend([
            f"RISK: decision={risk.get('decision', '?')} "
            f"checks={' '.join(f'{key}={value}' for key, value in checks.items()) or 'not run'}",
            f"  notes={risk.get('notes', result.get('rejection_reason', 'none'))}",
        ])
        execution = result.get("execution") or {}
        lines.append(
            f"EXECUTION: action={result.get('action', '?')} "
            f"order={execution.get('order_id', 'none')} status={execution.get('status', 'none')}"
        )
    return "\n".join(lines)


def notify_cycle_flow(
    results: list[dict], exits: list[dict], data: dict, cycle: dict
) -> list[dict[str, object]]:
    """Publish the complete per-cycle decision trail, including no-trade cycles."""
    sent: list[dict[str, object]] = []
    cycle_id = str(cycle.get("cycle_id", "unknown"))
    grouped = results or [{}]
    if not results:
        grouped = [{}]
    for result in grouped:
        symbol = str(result.get("symbol", "cycle"))
        message = _cycle_flow_message([result] if result else [], data, cycle, exits)
        for index, chunk in enumerate(_chunks(message)):
            sent.append(send_alert(
                "cycle_flow",
                chunk,
                event_id=f"cycle-flow:{cycle_id}:{symbol}:{index}",
            ))
    return sent


def notify_cycle_events(
    results: list[dict], exits: list[dict], data: dict, cycle: dict | None = None
) -> list[dict[str, object]]:
    """Publish idempotent lifecycle alerts from broker-facing cycle events."""
    sent: list[dict[str, object]] = []
    if cycle is not None and not cycle.get("dry_run"):
        sent.extend(notify_cycle_flow(results, exits, data, cycle))
    positions = {str(position.get("id")): position for position in data.get("positions", [])}

    for result in results:
        if result.get("action") not in {"EXECUTED", "ORDER_SUBMITTED"}:
            continue
        execution = result.get("execution") or {}
        order_id = str(execution.get("order_id", "unknown"))
        client_order_id = str(execution.get("client_order_id", order_id))
        symbol = result.get("symbol", "?")
        strategy = result.get("strategy_type", execution.get("strategy_type", "?"))
        sent.append(send_alert(
            "order_submitted",
            f"{symbol} {strategy} order={order_id} status={execution.get('status', 'unknown')}",
            event_id=f"entry-submitted:{client_order_id}",
        ))
        if str(execution.get("status", "")).split(".")[-1].lower() == "filled":
            sent.append(send_alert(
                "order_filled",
                f"{symbol} {strategy} order={order_id} qty={execution.get('qty', 0)}",
                event_id=f"entry-filled:{order_id}",
            ))

    for event in exits:
        reason = str(event.get("reason", ""))
        position_id = str(event.get("position_id") or event.get("id") or "unknown")
        position = positions.get(position_id, {})
        symbol = position.get("underlying", event.get("underlying", "?"))
        strategy = position.get("strategy_type", "?")
        order_id = str(event.get("order_id") or position.get("closing_order_id") or "unknown")
        if "estimated_realized_pl" in event or reason == "close_pending":
            sent.append(send_alert(
                "close_requested",
                f"{symbol} {strategy} reason={reason} order={order_id}",
                event_id=f"close-requested:{order_id}",
            ))
        elif reason == "entry_filled":
            sent.append(send_alert(
                "order_filled",
                f"{symbol} {strategy} order={position.get('order_id', 'unknown')} qty={position.get('qty', 0)}",
                event_id=f"entry-filled:{position.get('order_id', position_id)}",
            ))
        elif reason == "close_filled":
            sent.append(send_alert(
                "close_filled",
                f"{symbol} {strategy} order={order_id} realized_pl=${float(event.get('realized_pl', 0) or 0):.2f}",
                event_id=f"close-filled:{position_id}:{order_id}",
            ))
        elif reason in {"close_rejected", "entry_rejected"}:
            sent.append(send_alert(
                "critical_error",
                f"{symbol} {strategy} lifecycle={reason} order={order_id}",
                event_id=f"critical:{reason}:{position_id}:{order_id}",
            ))
    return sent
