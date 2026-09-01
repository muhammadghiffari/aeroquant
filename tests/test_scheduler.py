"""
tests/test_scheduler.py — Deterministic unit tests for scheduler and position tracker.

Tests broker state tracking, reconciliation, idempotency, and polling logic.
No network. No LLM. No Alpaca. Mock broker at interface level.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from execution.broker import AlpacaBroker, OrderStatus
from execution.scheduler import (
    BrokerPositionSnapshot,
    BrokerReconcileResult,
    BrokerPositionTracker,
)


class MockBroker:
    """Concrete mock implementing AlpacaBroker for tests."""

    def __init__(
        self,
        positions: list[BrokerPositionSnapshot] | None = None,
        open_order_ids: list[str] | None = None,
    ) -> None:
        self._positions = positions or []
        self._open_orders = open_order_ids or []
        self._submitted_orders: list[dict] = []
        self._cancelled_orders: list[str] = []
        self._account_id = "test-account"

    # Implement the interface
    def get_account(self):
        m = MagicMock()
        m.buying_power = "100000"
        m.cash = "100000"
        m.portfolio_value = "100000"
        m.equity = "100000"
        m.id = self._account_id
        return m

    def get_positions(self):
        return self._positions

    def get_order(self, order_id):
        for oid in self._open_orders:
            if oid == order_id:
                m = MagicMock()
                m.order_id = oid
                return m
        return None

    def submit_order(self, order_data, idempotency_key):
        self._submitted_orders.append({"data": order_data, "key": idempotency_key})
        return f"broker-order-{idempotency_key}"

    def cancel_order(self, order_id):
        self._cancelled_orders.append(order_id)

    def reconcile(self):
        return {
            "positions": self._positions,
            "open_order_ids": set(self._open_orders),
        }

    @property
    def account_id(self):
        return self._account_id

    def is_market_open(self):
        return True


class TestBrokerPositionTracker:
    """Tests for BrokerPositionTracker."""

    def test_empty_tracker_get_open_legs_returns_empty(self):
        tracker = BrokerPositionTracker()
        assert tracker.get_open_legs() == []

    def test_record_submitted(self):
        tracker = BrokerPositionTracker()
        p = BrokerPositionSnapshot(
            leg_id="leg-1",
            symbol="XSP241205P00550000",
            position_type="put",
            strike=555.0,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            quantity=-1,
            status="open",
        )
        tracker._positions["leg-1"] = p

        tracker.record_submitted("leg-1", "order-abc", "force_close")
        assert tracker.is_order_open("order-abc")
        assert tracker.is_leg_open("leg-1")

    def test_record_filled_clears_order(self):
        tracker = BrokerPositionTracker()
        p = BrokerPositionSnapshot(
            leg_id="leg-1", symbol="XSP241205P00550000",
            position_type="put", strike=555.0,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            quantity=-1, status="closing",
            close_order_id="order-foo", claimed_by="force_close",
        )
        tracker._positions["leg-1"] = p
        tracker._open_orders["order-foo"] = "leg-1"

        tracker.record_filled("order-foo", fill_price=1.50, fill_qty=1, now=datetime.now(timezone.utc))

        assert tracker.is_order_open("order-foo") is False
        assert tracker._positions["leg-1"].status == "confirmed_closed"

    def test_reconcile_adds_new_legs(self):
        tracker = BrokerPositionTracker()
        new_pos = BrokerPositionSnapshot(
            leg_id="leg-new",
            symbol="XSP241205P00550000",
            position_type="put", strike=555.0,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            quantity=-1, status="open",
        )
        result = tracker.reconcile(
            broker_positions=[new_pos],
            open_order_ids=set(),
            now=datetime.now(timezone.utc),
        )
        assert "leg-new" in {p.leg_id for p in result.snapshot}

    def test_reconcile_removes_closed_orders(self):
        tracker = BrokerPositionTracker()
        p = BrokerPositionSnapshot(
            leg_id="leg-closed",
            symbol="XSP241205P00550000",
            position_type="put", strike=555.0,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            quantity=-1, status="closing",
            close_order_id="order-gone",
        )
        tracker._positions["leg-closed"] = p
        tracker._open_orders["order-gone"] = "leg-closed"

        result = tracker.reconcile(
            broker_positions=[],
            open_order_ids=set(),
            now=datetime.now(timezone.utc),
        )

        assert "order-gone" in result.newly_closed_orders
        assert tracker.is_order_open("order-gone") is False

    def test_reconcile_idempotent(self):
        """Reconciling twice with same broker state is safe."""
        tracker = BrokerPositionTracker()
        pos = BrokerPositionSnapshot(
            leg_id="leg-1",
            symbol="XSP241205P00550000",
            position_type="put", strike=555.0,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            quantity=-1, status="open",
        )
        tracker._positions["leg-1"] = pos
        now = datetime.now(timezone.utc)
        r1 = tracker.reconcile(broker_positions=[pos], open_order_ids=set(), now=now)
        r2 = tracker.reconcile(broker_positions=[pos], open_order_ids=set(), now=now)
        assert r2 == r1  # stable

    def test_order_idempotency_key(self):
        """Same order_id submitted twice is tracked once."""
        tracker = BrokerPositionTracker()
        tracker.record_submitted("leg-1", "order-dup", "force_close")
        tracker.record_filled("order-dup", 1.5, 1, datetime.now(timezone.utc))
        # Second fill on same order: no-op
        tracker.record_filled("order-dup", 1.5, 1, datetime.now(timezone.utc))
        assert tracker.is_order_open("order-dup") is False

    def test_is_leg_open_scope_account(self):
        """Tracker is scoped by instance — two trackers don't share state."""
        t1, t2 = BrokerPositionTracker(), BrokerPositionTracker()
        t1._positions["leg-X"] = BrokerPositionSnapshot(
            leg_id="leg-X", symbol="XSP241205P00550000",
            position_type="put", strike=555.0,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            quantity=-1, status="open",
        )
        assert t1.is_leg_open("leg-X") is True
        assert t2.is_leg_open("leg-X") is False


class TestFailClosedAndForceCloseExecution:
    @staticmethod
    def _short_snapshot():
        return BrokerPositionSnapshot(
            leg_id="short-leg",
            symbol="XSP241205P00550000",
            position_type="put",
            strike=555.0,
            expiration=datetime(2024, 12, 5, 12, tzinfo=timezone.utc),
            quantity=-3,
            status="open",
            bid=1.00,
            ask=1.10,
            mark_price=1.05,
        )

    def test_broker_outage_does_not_imply_position_closed(self):
        tracker = BrokerPositionTracker()
        position = self._short_snapshot()
        tracker._positions[position.leg_id] = position

        result = tracker.reconcile(
            broker_positions=None,
            open_order_ids=None,
            now=datetime(2024, 12, 4, 12, tzinfo=timezone.utc),
            broker_error="timeout",
        )

        assert result.broker_snapshot_available is False
        assert result.newly_confirmed_legs == []
        assert tracker.is_leg_open(position.leg_id) is True

    def test_unknown_open_order_snapshot_keeps_close_claim(self):
        tracker = BrokerPositionTracker()
        position = self._short_snapshot()
        position.status = "closing"
        position.close_order_id = "close-1"
        tracker._positions[position.leg_id] = position
        tracker._open_orders["close-1"] = position.leg_id

        result = tracker.reconcile(
            broker_positions=[position],
            open_order_ids=None,
            now=datetime(2024, 12, 4, 12, tzinfo=timezone.utc),
        )

        assert result.newly_closed_orders == []
        assert tracker.is_order_open("close-1") is True
        assert tracker._positions[position.leg_id].status == "closing"

    def test_force_close_path_is_typed_idempotent_and_fill_confirmed(self):
        from alpaca.trading.enums import OrderSide, OrderType, PositionIntent
        from alpaca.trading.requests import LimitOrderRequest
        from execution.force_close_guard import ForceCloseGuard
        from execution.order_dispatcher import CloseOrderDispatcher
        from execution.scheduler import ForceCloseExecutor

        now = datetime(2024, 12, 4, 12, tzinfo=timezone.utc)
        position = self._short_snapshot()
        broker = MagicMock()
        broker.reconcile.return_value = {"positions": [position], "open_order_ids": set()}
        broker.find_order_by_client_order_id.return_value = None
        broker.submit_order.return_value = "close-1"
        tracker = BrokerPositionTracker()
        guard = ForceCloseGuard(dte_threshold_hours=24)
        close_dispatcher = CloseOrderDispatcher(broker, tracker=tracker, clock=lambda: now)
        executor = ForceCloseExecutor(tracker, guard, close_dispatcher)

        first = executor.run_once(broker, now)

        assert len(first.close_orders) == 1
        assert first.dispatch_results[0].dispatched is True
        request = broker.submit_order.call_args.kwargs["order_data"]
        assert isinstance(request, LimitOrderRequest)
        assert request.qty == 3
        assert request.type == OrderType.LIMIT
        assert request.side == OrderSide.BUY
        assert request.position_intent == PositionIntent.BUY_TO_CLOSE
        assert tracker._positions["short-leg"].status == "closing"

        broker.reconcile.return_value = {"positions": [position], "open_order_ids": {"close-1"}}
        second = executor.run_once(broker, now)
        assert second.close_orders == []
        assert broker.submit_order.call_count == 1

        confirmed = executor.record_broker_confirmed_fill("close-1", 1.15, 3, now)
        assert confirmed is not None
        assert confirmed.status == "confirmed_closed"


    def test_reconcile_from_broker_outage_retains_live_position(self):
        tracker = BrokerPositionTracker()
        position = self._short_snapshot()
        tracker._positions[position.leg_id] = position
        broker = MagicMock()
        broker.reconcile.side_effect = TimeoutError("offline")

        result = tracker.reconcile_from_broker(
            broker, datetime(2024, 12, 4, 12, tzinfo=timezone.utc)
        )

        assert result.broker_snapshot_available is False
        assert result.newly_confirmed_legs == []
        assert tracker._positions[position.leg_id].status == "open"
