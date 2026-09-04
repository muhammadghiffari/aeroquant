"""Safety tests for broker-to-ledger position reconciliation."""
from types import SimpleNamespace

from execution import position_manager


def test_partial_broker_position_requires_recovery_without_submitting_close(monkeypatch):
    data = {
        "positions": [
            {
                "id": "position-1",
                "status": "OPEN",
                "underlying": "SPY",
                "qty": 1,
                "legs": [
                    {"symbol": "SPY260902P00500000", "action": "BUY", "qty": 1},
                    {"symbol": "SPY260902P00505000", "action": "SELL", "qty": 1},
                ],
            }
        ],
        "daily": {},
    }

    class FakeClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="SPY260902P00500000", qty="1")]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(
        position_manager.executor,
        "close_single_leg",
        lambda *_args: (_ for _ in ()).throw(AssertionError("reconciliation submitted a close")),
    )

    events = position_manager.reconcile_with_broker(data)

    assert data["positions"][0]["status"] == "RECOVERY_REQUIRED"
    assert data["positions"][0]["recovery_reason"] == "partial_broker_position"
    assert events == [{"id": "position-1", "underlying": "SPY", "reason": "partial_broker_position"}]


def test_recovery_required_position_counts_toward_exposure(monkeypatch):
    data = {
        "positions": [
            {"status": "RECOVERY_REQUIRED", "qty": 1, "max_loss_usd": 250.0},
        ],
        "daily": {},
    }

    class FakeClient:
        def get_account(self):
            return SimpleNamespace(equity="10000")

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    assert position_manager.open_positions_count(data) == 1
    assert position_manager.exposure_pct(data) == 2.5


def test_recovery_position_closes_when_broker_position_is_gone(monkeypatch):
    data = {
        "positions": [{
            "id": "position-recovery",
            "status": "RECOVERY_REQUIRED",
            "underlying": "SPY",
            "qty": 1,
            "order_id": "entry-1",
            "net_credit_or_debit_per_unit": -4.0,
            "legs": [{"symbol": "SPY260902C00500000", "action": "BUY", "qty": 1}],
        }],
        "daily": {},
    }

    class FakeClient:
        def get_all_positions(self):
            return []

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(position_manager, "_external_close_realized_pl", lambda _position: None)

    events = position_manager.reconcile_with_broker(data)

    assert data["positions"][0]["status"] == "CLOSED"
    assert events == [{
        "id": "position-recovery",
        "underlying": "SPY",
        "reason": "closed_externally_reconciled",
    }]


def test_reconciliation_updates_long_option_entry_debit_from_broker_fill(monkeypatch):
    data = {
        "positions": [
            {
                "id": "position-1",
                "status": "OPEN",
                "underlying": "SPY",
                "strategy_type": "LONG_CALL",
                "qty": 1,
                "net_credit_or_debit_per_unit": -4.63,
                "legs": [{"symbol": "SPY260903C00769000", "action": "BUY", "qty": 1}],
            }
        ],
        "daily": {},
    }

    class FakeClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="SPY260903C00769000", qty="1", avg_entry_price="4.82")]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    assert position_manager.reconcile_with_broker(data) == []
    assert data["positions"][0]["net_credit_or_debit_per_unit"] == -4.82


def test_external_long_option_close_uses_broker_fill_for_realized_pl(monkeypatch):
    position = {
        "order_id": "entry-1",
        "qty": 1,
        "net_credit_or_debit_per_unit": -6.76,
        "legs": [{"symbol": "SPY260903C00765000", "action": "BUY", "qty": 1}],
    }

    class FakeClient:
        def get_order_by_id(self, order_id):
            return SimpleNamespace(filled_avg_price="6.76", filled_qty="1")

        def get_orders(self, filter=None):
            return [SimpleNamespace(
                symbol="SPY260903C00765000", side="sell", status="filled",
                filled_avg_price="6.49", filled_qty="1",
            )]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    assert position_manager._external_close_realized_pl(position) == -27.0


def test_imports_filled_mleg_when_worker_crashed_before_ledger_save(monkeypatch):
    data = {"positions": [], "daily": {}}

    class FakeClient:
        def get_all_positions(self):
            return [
                SimpleNamespace(symbol="SPY260903P00766000", qty="1"),
                SimpleNamespace(symbol="SPY260903P00767000", qty="-1"),
            ]

        def get_orders(self, filter=None):
            return [
                SimpleNamespace(
                    id="order-1",
                    client_order_id="agent-spy-recovered",
                    status="filled",
                    filled_qty="1",
                    qty="1",
                    filled_avg_price="-0.29",
                    filled_at="2026-08-27T15:00:00+00:00",
                    legs=[
                        SimpleNamespace(
                            symbol="SPY260903P00767000",
                            side="sell",
                            ratio_qty="1",
                            position_intent="sell_to_open",
                        ),
                        SimpleNamespace(
                            symbol="SPY260903P00766000",
                            side="buy",
                            ratio_qty="1",
                            position_intent="buy_to_open",
                        ),
                    ],
                )
            ]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    events = position_manager.reconcile_untracked_filled_orders(data)

    assert events == [{"id": "order-1", "reason": "imported_filled_order", "underlying": "SPY"}]
    position = data["positions"][0]
    assert position["status"] == "OPEN"
    assert position["strategy_type"] == "BULL_PUT_SPREAD"
    assert position["net_credit_or_debit_per_unit"] == 0.29
    assert position["max_loss_usd"] == 71.0
    assert {leg["action"] for leg in position["legs"]} == {"BUY", "SELL"}


def test_imports_filled_single_leg_long_order_when_worker_crashed(monkeypatch):
    data = {"positions": [], "daily": {}}

    class FakeClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL260902C00297500", qty="1")]

        def get_orders(self, filter=None):
            return [
                SimpleNamespace(
                    id="order-2",
                    client_order_id="agent-aapl-recovered",
                    status="filled",
                    symbol="AAPL260902C00297500",
                    side="buy",
                    position_intent="buy_to_open",
                    filled_qty="1",
                    qty="1",
                    filled_avg_price="20.06",
                    filled_at="2026-08-27T15:00:00+00:00",
                    legs=None,
                )
            ]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())

    events = position_manager.reconcile_untracked_filled_orders(data)

    assert events == [{"id": "order-2", "reason": "imported_filled_order", "underlying": "AAPL"}]
    position = data["positions"][0]
    assert position["status"] == "OPEN"
    assert position["strategy_type"] == "LONG_CALL"
    assert position["net_credit_or_debit_per_unit"] == -20.06
    assert position["legs"] == [{
        "action": "BUY",
        "symbol": "AAPL260902C00297500",
        "strike": 297.5,
        "expiry": "2026-09-02",
        "opt_type": "call",
        "qty": 1,
    }]


def test_imports_filled_order_with_fill_based_exit_levels(monkeypatch):
    data = {"positions": [], "daily": {}}

    class FakeClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL260911C00317500", qty="1")]

        def get_orders(self, filter=None):
            return [
                SimpleNamespace(
                    id="order-momentum",
                    client_order_id="agent-aapl-momentum",
                    status="filled",
                    symbol="AAPL260911C00317500",
                    side="buy",
                    position_intent="buy_to_open",
                    filled_qty="1",
                    qty="1",
                    filled_avg_price="4.80",
                    filled_at="2026-08-27T15:00:00+00:00",
                    legs=None,
                )
            ]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(
        position_manager.operational_store,
        "get_order_intent_by_client_id",
        lambda _client_order_id: {"proposal_json": '{"_entry_price": 4.75}'},
    )

    events = position_manager.reconcile_untracked_filled_orders(data)

    assert events == [{"id": "order-momentum", "reason": "imported_filled_order", "underlying": "AAPL"}]
    position = data["positions"][0]
    assert position["entry_price"] == 4.80
    assert position["take_profit_price"] == 6.48
    assert position["stop_loss_price"] == 2.40
    assert "time_stop_minutes" not in position


def test_reconciles_existing_position_with_fill_based_exit_levels(monkeypatch):
    data = {
        "positions": [{
            "id": "position-momentum",
            "underlying": "AAPL",
            "strategy_type": "LONG_CALL",
            "client_order_id": "agent-aapl-momentum",
            "order_id": "order-momentum",
            "status": "OPEN",
            "legs": [{
                "action": "BUY",
                "symbol": "AAPL260911C00317500",
                "strike": 317.5,
                "expiry": "2026-09-11",
                "opt_type": "call",
                "qty": 1,
            }],
        }],
        "daily": {},
    }

    class FakeClient:
        def get_all_positions(self):
            return [SimpleNamespace(
                symbol="AAPL260911C00317500",
                qty="1",
                avg_entry_price="4.75",
            )]

    monkeypatch.setattr(position_manager.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(
        position_manager.operational_store,
        "get_order_intent_by_client_id",
        lambda _client_order_id: {"proposal_json": '{"_entry_price": 4.75}'},
    )

    assert position_manager.reconcile_with_broker(data) == []
    position = data["positions"][0]
    assert position["entry_price"] == 4.75
    assert position["take_profit_price"] == 6.41
    assert position["stop_loss_price"] == 2.38
    assert "time_stop_minutes" not in position
