"""Deterministic exit policy tests for long options."""
from datetime import date
from types import SimpleNamespace

from execution.exit_policy import exit_decision
from execution import position_manager


def _position(entry_debit=-10.0, expiry="2026-09-03"):
    return {
        "id": "position-1",
        "status": "OPEN",
        "underlying": "AAPL",
        "qty": 1,
        "strategy_type": "LONG_CALL",
        "net_credit_or_debit_per_unit": entry_debit,
        "legs": [{"action": "BUY", "symbol": "AAPL260903C00300000", "expiry": expiry}],
    }


def test_closes_long_option_when_executable_profit_clears_buffer():
    decision = exit_decision(
        _position(),
        {"AAPL260903C00300000": SimpleNamespace(bid=10.20, ask=10.40)},
        today=date(2026, 8, 28),
    )

    assert decision["reason"] == "take_profit_executable"
    assert decision["estimated_realized_pl"] == 20.0


def test_closes_long_option_at_configured_stop_loss():
    decision = exit_decision(
        _position(),
        {"AAPL260903C00300000": SimpleNamespace(bid=5.0, ask=5.2)},
        today=date(2026, 8, 28),
    )

    assert decision["reason"] == "stop_loss_executable"
    assert decision["estimated_realized_pl"] == -500.0


def test_dte_force_close_precedes_profit_target():
    decision = exit_decision(
        _position(expiry="2026-08-29"),
        {"AAPL260903C00300000": SimpleNamespace(bid=20.0, ask=20.2)},
        today=date(2026, 8, 28),
    )

    assert decision["reason"] == "force_close_pre_expiry"


def test_final_close_date_precedes_all_other_exit_rules():
    decision = exit_decision(
        _position(),
        {"AAPL260903C00300000": SimpleNamespace(bid=2.0, ask=2.2)},
        today=date(2026, 9, 4),
    )

    assert decision["reason"] == "force_close_final_deadline"


def test_position_manager_uses_executable_profit_exit_for_long_option(monkeypatch):
    data = {"positions": [_position()], "daily": {}}
    contract = SimpleNamespace(bid=10.20, ask=10.40, mid=10.30)
    monkeypatch.setattr(position_manager.config, "market_date", lambda: date(2026, 8, 28))
    monkeypatch.setattr(position_manager, "reconcile_order_intents", lambda: [])
    monkeypatch.setattr(position_manager, "reconcile_untracked_filled_orders", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_unfilled", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_with_broker", lambda _data: [])
    monkeypatch.setattr(
        position_manager,
        "_chain_map",
        lambda _underlyings: {"AAPL260903C00300000": contract},
    )
    monkeypatch.setattr(
        position_manager.executor,
        "close_position",
        lambda *_args: {"order_id": "close-1", "status": "accepted", "estimated_realized_pl": 20.0},
    )

    exits = position_manager.manage_positions(data)

    assert exits[0]["reason"] == "take_profit_executable"
