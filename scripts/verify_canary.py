"""Verify the acceptance conditions for one paper-trading canary cycle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from data_engine import alpaca_client


_ORDER_ACTIONS = {"ORDER_SUBMITTED", "EXECUTED"}


def _value(item, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _verify_stage_delivery(report: dict, symbol: str) -> None:
    cycle_id = str(report.get("cycle_id", ""))
    events = report.get("stage_events")
    if not isinstance(events, list) or not events:
        _fail("Telegram stage events are missing")
    prefix = f"{cycle_id}:{symbol}:"
    completed = False
    for event in events:
        event_id = str(event.get("event_id", ""))
        if not event_id.startswith(prefix):
            _fail("Telegram stage event ID does not match the canary")
        if not event.get("sent") and event.get("reason") != "duplicate":
            _fail("Telegram stage event was not delivered")
        if ":CYCLE_COMPLETED:" in event_id:
            completed = True
    if not completed:
        _fail("Telegram CYCLE_COMPLETED event is missing")


def verify_canary_report(
    report: dict,
    symbol: str,
    broker_orders=None,
) -> dict[str, object]:
    """Raise on an unsafe report and return a secret-free acceptance summary."""
    if not config.PAPER_TRADE:
        _fail("canary requires paper trading")
    if config.FINAL_CLOSE_DATE <= config.market_date():
        _fail("FINAL_CLOSE_DATE must be after the current market date")

    expected_symbol = symbol.upper()
    symbols = report.get("symbols")
    if not isinstance(symbols, list) or len(symbols) != 1 or str(symbols[0]).upper() != expected_symbol:
        _fail("canary report must contain exactly one symbol")
    cycle_id = str(report.get("cycle_id", "")).strip()
    if not cycle_id:
        _fail("canary cycle_id is missing")

    expected_account = str(config.EXPECTED_ALPACA_ACCOUNT_ID or "").strip()
    account_id = str((report.get("account") or {}).get("account_id", "")).strip()
    if not expected_account:
        _fail("expected account ID is not configured")
    if account_id != expected_account:
        _fail("canary account ID mismatch")
    _verify_stage_delivery(report, expected_symbol)

    results = report.get("results")
    if not isinstance(results, list) or not results:
        _fail("canary report must contain a symbol result")
    if any(str(result.get("symbol", "")).upper() != expected_symbol for result in results):
        _fail("canary result symbol mismatch")

    executions = [item for item in results if item.get("action") in _ORDER_ACTIONS]
    if len(executions) > 1:
        _fail("canary may submit at most one order")

    summary: dict[str, object] = {
        "status": "accepted",
        "cycle_id": cycle_id,
        "symbol": expected_symbol,
        "order_submitted": bool(executions),
    }
    if not executions:
        if len(results) != 1:
            _fail("canary no-order result must contain exactly one result")
        result = results[0]
        action = str(result.get("action", ""))
        if not (action == "WAIT" or action.startswith("WAIT_")
                or action == "REJECTED" or action.startswith("REJECTED_")):
            _fail("canary no-order result must be WAIT or REJECTED")
        proposal = (result.get("reports") or {}).get("proposal") or {}
        reason = result.get("rejection_reason") or result.get("error") or proposal.get("rationale")
        if not str(reason or "").strip():
            _fail("safe canary result must contain a reason")
        summary["reason"] = str(reason)[:240]
        return summary

    execution = executions[0].get("execution") or {}
    client_order_id = str(_value(execution, "client_order_id", "")).strip()
    if not client_order_id:
        _fail("canary order is missing client_order_id")
    if broker_orders is None:
        _fail("broker orders are required to reconcile the canary")
    matched = any(
        str(_value(order, "client_order_id", "")).strip() == client_order_id
        for order in broker_orders
    )
    if not matched:
        _fail("canary order was not found at the broker")
    summary["client_order_id"] = client_order_id
    summary["order_id"] = str(_value(execution, "order_id", ""))
    return summary


def _latest_report() -> dict:
    reports = sorted(config.REPORTS_DIR.glob("*_cycle_report.json"))
    if not reports:
        _fail("no cycle report found")
    try:
        with open(reports[-1], encoding="utf-8") as stream:
            report = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read latest cycle report: {type(exc).__name__}")
    if not isinstance(report, dict):
        _fail("latest cycle report is not an object")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify one paper canary cycle")
    parser.add_argument("--symbol", required=True, help="the one canary symbol")
    args = parser.parse_args(argv)
    report = _latest_report()
    executions = [
        item for item in report.get("results", [])
        if item.get("action") in _ORDER_ACTIONS
    ]
    broker_orders = None
    if executions:
        client = alpaca_client.trading_client()
        broker_orders = alpaca_client.safe("get_orders", client.get_orders)
    result = verify_canary_report(report, args.symbol, broker_orders=broker_orders)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
