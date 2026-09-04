from datetime import date
from pathlib import Path
import subprocess
import sys

import config
from scripts.verify_canary import verify_canary_report


def _base_report(action="WAIT", reason="quant gate rejected"):
    return {
        "cycle_id": "cycle-1",
        "symbols": ["SPY"],
        "account": {"account_id": "acct-1"},
        "stage_events": [
            {"sent": True, "event_id": "cycle-1:SPY:CYCLE_STARTED:0"},
            {"sent": True, "event_id": "cycle-1:SPY:CYCLE_COMPLETED:1"},
        ],
        "results": [{"symbol": "SPY", "action": action, "rejection_reason": reason}],
    }


def test_canary_accepts_single_symbol_safe_wait(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADE", True)
    monkeypatch.setattr(config, "EXPECTED_ALPACA_ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(config, "FINAL_CLOSE_DATE", date(2099, 1, 1))
    monkeypatch.setattr(config, "market_date", lambda: date(2026, 9, 3))

    result = verify_canary_report(_base_report(), "SPY")

    assert result["status"] == "accepted"
    assert result["cycle_id"] == "cycle-1"
    assert result["order_submitted"] is False


def test_canary_rejects_multi_symbol_report(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADE", True)
    monkeypatch.setattr(config, "EXPECTED_ALPACA_ACCOUNT_ID", "acct-1")
    report = _base_report()
    report["symbols"] = ["SPY", "QQQ"]

    try:
        verify_canary_report(report, "SPY")
    except RuntimeError as exc:
        assert "exactly one symbol" in str(exc)
    else:
        raise AssertionError("unsafe canary report was accepted")


def test_canary_reconciles_submitted_order_by_client_order_id(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADE", True)
    monkeypatch.setattr(config, "EXPECTED_ALPACA_ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(config, "FINAL_CLOSE_DATE", date(2099, 1, 1))
    monkeypatch.setattr(config, "market_date", lambda: date(2026, 9, 3))
    report = _base_report(action="ORDER_SUBMITTED", reason=None)
    report["results"][0]["execution"] = {
        "client_order_id": "agent-spy-1",
        "order_id": "broker-order-1",
        "status": "accepted",
    }

    result = verify_canary_report(
        report,
        "SPY",
        broker_orders=[{"id": "broker-order-1", "client_order_id": "agent-spy-1"}],
    )

    assert result["status"] == "accepted"
    assert result["order_submitted"] is True
    assert result["client_order_id"] == "agent-spy-1"


def test_canary_rejects_multiple_order_results(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADE", True)
    monkeypatch.setattr(config, "EXPECTED_ALPACA_ACCOUNT_ID", "acct-1")
    report = _base_report(action="ORDER_SUBMITTED", reason=None)
    report["results"].append({
        "symbol": "SPY", "action": "EXECUTED", "execution": {"client_order_id": "agent-spy-2"},
    })

    try:
        verify_canary_report(report, "SPY")
    except RuntimeError as exc:
        assert "at most one" in str(exc)
    else:
        raise AssertionError("multiple canary orders were accepted")


def test_canary_accepts_wait_with_proposal_rationale_as_reason(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADE", True)
    monkeypatch.setattr(config, "EXPECTED_ALPACA_ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(config, "FINAL_CLOSE_DATE", date(2099, 1, 1))
    monkeypatch.setattr(config, "market_date", lambda: date(2026, 9, 3))
    report = _base_report(action="WAIT", reason=None)
    report["results"][0]["reports"] = {
        "proposal": {"rationale": "fallback: no-trade because provider output was invalid"},
    }

    result = verify_canary_report(report, "SPY")

    assert result["status"] == "accepted"
    assert "fallback: no-trade" in result["reason"]


def test_canary_script_supports_direct_invocation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/verify_canary.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
