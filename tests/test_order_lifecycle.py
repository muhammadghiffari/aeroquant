from types import SimpleNamespace

from execution import position_manager


def _pending_position():
    return {
        "id": "position-1",
        "status": "PENDING_ENTRY",
        "order_id": "order-1",
        "underlying": "SPY",
        "strategy_type": "LONG_CALL",
        "qty": 1,
        "net_credit_or_debit_per_unit": -4.0,
        "legs": [{"action": "BUY", "symbol": "SPY260903C00769000", "qty": 1, "expiry": "2026-09-03", "opt_type": "call", "strike": 769.0}],
    }


def test_pending_entry_becomes_open_only_after_broker_fill(monkeypatch):
    data = {"positions": [_pending_position()], "daily": {}}

    class FakeClient:
        def get_order_by_id(self, order_id):
            assert order_id == "order-1"
            return SimpleNamespace(status="filled", filled_avg_price="4.82", filled_qty="1")

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    events = position_manager.reconcile_pending_entries(data)

    assert data["positions"][0]["status"] == "OPEN"
    assert data["positions"][0]["net_credit_or_debit_per_unit"] == -4.82
    assert data["positions"][0]["entry_status"] == "filled"
    assert data["positions"][0]["max_loss_usd"] == 482.0
    assert events[0]["reason"] == "entry_filled"


def test_pending_entry_reanchors_exit_levels_to_actual_fill(monkeypatch):
    position = _pending_position()
    position.update({"entry_price": 4.0, "take_profit_price": 5.0, "stop_loss_price": 3.0})
    data = {"positions": [position], "daily": {}}

    class FakeClient:
        def get_order_by_id(self, _order_id):
            return SimpleNamespace(status="filled", filled_avg_price="4.82", filled_qty="1")

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    position_manager.reconcile_pending_entries(data)

    assert position["entry_price"] == 4.82
    assert position["take_profit_price"] == 6.51
    assert position["stop_loss_price"] == 2.41
    assert "time_stop_minutes" not in position


def test_pending_entry_is_closed_when_broker_rejects_it(monkeypatch):
    data = {"positions": [_pending_position()], "daily": {}}

    class FakeClient:
        def get_order_by_id(self, order_id):
            return SimpleNamespace(status="rejected")

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    events = position_manager.reconcile_pending_entries(data)

    assert data["positions"][0]["status"] == "CLOSED"
    assert data["positions"][0]["exit_reason"] == "entry_rejected"
    assert events[0]["reason"] == "entry_rejected"


def test_accepted_close_remains_closing_until_fill(monkeypatch):
    data = {
        "positions": [{
            "id": "position-1", "status": "CLOSING", "order_id": "entry-1",
            "closing_order_id": "close-1", "underlying": "SPY", "strategy_type": "LONG_CALL",
            "qty": 1, "net_credit_or_debit_per_unit": -4.0,
            "legs": [{"action": "BUY", "symbol": "SPY260903C00769000", "qty": 1}],
        }],
        "daily": {},
    }

    class FakeClient:
        def get_order_by_id(self, order_id):
            return SimpleNamespace(status="accepted")

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    events = position_manager.reconcile_closing_positions(data)

    assert data["positions"][0]["status"] == "CLOSING"
    assert events[0]["reason"] == "close_pending"


def test_filled_close_reports_broker_order_and_realized_pl(monkeypatch):
    data = {
        "positions": [{
            "id": "position-1", "status": "CLOSING", "order_id": "entry-1",
            "closing_order_id": "close-1", "underlying": "SPY", "strategy_type": "LONG_CALL",
            "qty": 1, "net_credit_or_debit_per_unit": -4.0,
            "legs": [{"action": "BUY", "symbol": "SPY260903C00769000", "qty": 1}],
        }],
        "daily": {},
    }

    class FakeClient:
        def get_order_by_id(self, order_id):
            assert order_id == "close-1"
            return SimpleNamespace(status="filled", filled_avg_price="4.10")

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    events = position_manager.reconcile_closing_positions(data)

    assert events == [{
        "id": "position-1", "reason": "close_filled", "realized_pl": 10.0,
        "order_id": "close-1",
    }]


def test_manage_positions_reconciles_filled_pending_entry_before_broker_drift(monkeypatch):
    data = {"positions": [_pending_position()], "daily": {}}

    class FakeClient:
        def get_order_by_id(self, order_id):
            return SimpleNamespace(status="filled", filled_avg_price="4.82", filled_qty="1")

        def get_all_positions(self):
            return []

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(position_manager.operational_store, "unresolved_intents", lambda: [])
    monkeypatch.setattr(position_manager, "reconcile_closing_positions", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_untracked_filled_orders", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_unfilled", lambda _data: [])
    monkeypatch.setattr(position_manager, "_chain_map", lambda _underlyings: {})

    events = position_manager.manage_positions(data)

    assert data["positions"][0]["status"] == "CLOSED"
    assert [event["reason"] for event in events] == [
        "entry_filled", "closed_externally_reconciled",
    ]
