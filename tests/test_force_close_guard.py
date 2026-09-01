"""
tests/test_force_close_guard.py — Deterministic unit tests for ForceCloseGuard.

Tests PRD §9: force-close before expiry.
Tests idempotency, broker-confirmed close state, edge cases.
No network. No LLM. No Alpaca.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from execution.force_close_guard import (
    ForceCloseGuard,
    ForceCloseJob,
    ShortLegPosition,
    CloseOrder,
    PositionStatus,
    CLAIMED_BY_FORCE_CLOSE,
    DEFAULT_DTE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_short_leg(
    leg_id: str = "leg-001",
    symbol: str = "XSP241205P00550000",
    quantity: int = -1,
    expiration_hours_from_now: float = 48.0,
    status: PositionStatus = PositionStatus.OPEN,
    bid: float = 1.00,
    ask: float = 1.01,
    mark: float = 1.005,
    close_order_id: str | None = None,
) -> ShortLegPosition:
    """Helper to create a ShortLegPosition with configurable DTE."""
    now = datetime(2024, 12, 4, 12, 0, 0, tzinfo=timezone.utc)
    expiry = now + timedelta(hours=expiration_hours_from_now)
    return ShortLegPosition(
        leg_id=leg_id,
        symbol=symbol,
        position_type="put",
        strike=555.0,
        expiration=expiry,
        quantity=quantity,
        status=status,
        close_order_id=close_order_id,
        current_bid=bid,
        current_ask=ask,
        mark_price=mark,
    )


def make_now() -> datetime:
    return datetime(2024, 12, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Basic Functionality Tests
# ---------------------------------------------------------------------------

class TestForceCloseBasic:
    def test_no_positions_returns_empty(self):
        """No open positions → empty list."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        orders = guard.assess(open_legs=[], now=make_now())
        assert orders == []

    def test_position_with_sufficient_dte_returns_empty(self):
        """DTE > threshold → no force-close needed."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="safe-leg", expiration_hours_from_now=48.0)
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert orders == []

    def test_position_at_threshold_creates_order(self):
        """DTE <= threshold → force-close order created."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="urgent-leg", expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        assert orders[0].leg_id == "urgent-leg"
        assert orders[0].action == "BUY_TO_CLOSE"  # short put → BUY_TO_CLOSE
        assert orders[0].limit_price > 0

    def test_short_put_buy_to_close(self):
        """Short put → BUY_TO_CLOSE action."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(quantity=-1, expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        assert orders[0].action == "BUY_TO_CLOSE"

    def test_long_put_sell_to_close(self):
        """Long put → SELL_TO_CLOSE action."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="long-leg", quantity=1, expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        assert orders[0].action == "SELL_TO_CLOSE"

    def test_multiple_legs_creates_multiple_orders(self):
        """Multiple urgent legs → multiple orders."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        legs = [
            make_short_leg(leg_id="leg-1", expiration_hours_from_now=20.0),
            make_short_leg(leg_id="leg-2", expiration_hours_from_now=22.0),
            make_short_leg(leg_id="leg-3", expiration_hours_from_now=72.0),  # safe
        ]
        orders = guard.assess(open_legs=legs, now=make_now())

        assert len(orders) == 2
        leg_ids = {o.leg_id for o in orders}
        assert leg_ids == {"leg-1", "leg-2"}


# ---------------------------------------------------------------------------
# Idempotency Tests
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_already_claimed_skipped(self):
        """Leg already claimed by force-close → skipped (idempotent)."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="claimed-leg", expiration_hours_from_now=20.0)

        # First call: creates order and claims leg
        orders1 = guard.assess(open_legs=[leg], now=make_now())
        assert len(orders1) == 1

        # Second call: leg is claimed → no new order
        orders2 = guard.assess(open_legs=[leg], now=make_now())
        assert orders2 == []

    def test_claimed_legs_tracked(self):
        """get_claimed_legs() returns tracked legs."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="track-leg", expiration_hours_from_now=20.0)
        guard.assess(open_legs=[leg], now=make_now())

        claimed = guard.get_claimed_legs()
        assert "track-leg" in claimed

    def test_on_close_confirmed_clears_claim(self):
        """on_close_confirmed() removes leg from claimed set."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="confirmed-leg", expiration_hours_from_now=20.0)
        guard.assess(open_legs=[leg], now=make_now())

        guard.on_close_confirmed(
            leg_id="confirmed-leg",
            order_id="order-123",
            fill_price=1.00,
            now=make_now(),
        )

        assert "confirmed-leg" not in guard.get_claimed_legs()

    def test_on_close_cancelled_releases_claim(self):
        """on_close_cancelled() releases claim so assess() can retry."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="cancelled-leg", expiration_hours_from_now=20.0)
        guard.assess(open_legs=[leg], now=make_now())

        guard.on_close_cancelled(
            leg_id="cancelled-leg",
            reason="rejected",
            now=make_now(),
        )

        assert "cancelled-leg" not in guard.get_claimed_legs()
        # Next assessment should create a new order
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert len(orders) == 1


# ---------------------------------------------------------------------------
# Broker State Compatibility Tests
# ---------------------------------------------------------------------------

class TestBrokerStateCompatibility:
    def test_already_closing_skipped(self):
        """Status = CLOSING → already has close order, skip."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(
            leg_id="closing-leg",
            expiration_hours_from_now=20.0,
            status=PositionStatus.CLOSING,
            close_order_id="order-existing",
        )
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert orders == []

    def test_already_closed_skipped(self):
        """Status = CLOSED → no order needed."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(
            leg_id="closed-leg",
            expiration_hours_from_now=20.0,
            status=PositionStatus.CLOSED,
        )
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert orders == []

    def test_expired_natural_logged_as_incident(self):
        """DTE <= 0 with OPEN status → PRD §9 violation logged."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        # DTE = -2 hours (already expired, but still marked OPEN — PRD §9 breach!)
        leg = make_short_leg(
            leg_id="expired-leg",
            expiration_hours_from_now=-2.0,
            status=PositionStatus.OPEN,
        )
        orders = guard.assess(open_legs=[leg], now=make_now())
        # No order created for already-expired leg
        assert orders == []


# ---------------------------------------------------------------------------
# Limit Price Tests
# ---------------------------------------------------------------------------

class TestLimitPrice:
    def test_limit_price_5pct_padded_buy_to_close(self):
        """BUY_TO_CLOSE: limit = ask * 1.05 (pad up toward adverse)."""
        guard = ForceCloseGuard(dte_threshold_hours=24, pad_pct=0.05)
        leg = make_short_leg(
            leg_id="pad-test",
            expiration_hours_from_now=20.0,
            bid=1.00,
            ask=1.10,  # 10 cent spread
        )
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        # Expected: ask * 1.05 = 1.10 * 1.05 = 1.155
        expected = 1.10 * 1.05
        assert abs(orders[0].limit_price - expected) < 0.001

    def test_limit_price_fallback_to_mark(self):
        """No bid/ask → use mark price * 1.05."""
        guard = ForceCloseGuard(dte_threshold_hours=24, pad_pct=0.05)
        leg = make_short_leg(
            leg_id="mark-test",
            expiration_hours_from_now=20.0,
            bid=None,
            ask=None,
            mark=2.00,
        )
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        assert orders[0].limit_price == pytest.approx(2.00 * 1.05)

    def test_emergency_floor_price(self):
        """No bid/ask/mark → emergency floor price (0.01)."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = ShortLegPosition(
            leg_id="no-price-leg",
            symbol="XSP241205P00550000",
            position_type="put",
            strike=555.0,
            expiration=datetime(2024, 12, 4, 14, 0, 0, tzinfo=timezone.utc),
            quantity=-1,
            status=PositionStatus.OPEN,
            current_bid=None,
            current_ask=None,
            mark_price=None,
        )
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        assert orders[0].limit_price == 0.01  # emergency floor


# ---------------------------------------------------------------------------
# ForceCloseJob Tests
# ---------------------------------------------------------------------------

class TestForceCloseJob:
    def test_job_created(self):
        """ForceCloseJob holds multiple CloseOrders."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        legs = [
            make_short_leg(leg_id=f"job-leg-{i}", expiration_hours_from_now=20.0)
            for i in range(3)
        ]
        orders = guard.assess(open_legs=legs, now=make_now())

        job = ForceCloseJob(
            job_id="job-001",
            close_orders=orders,
            status="pending",
        )

        assert len(job.close_orders) == 3
        assert job.status == "pending"

    def test_job_status_transitions(self):
        """Job status transitions from pending → submitted → completed."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="job-transition", expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        job = ForceCloseJob(job_id="job-002", close_orders=orders)

        assert job.status == "pending"
        job.status = "submitted"
        assert job.status == "submitted"
        job.status = "completed"
        assert job.status == "completed"


# ---------------------------------------------------------------------------
# DTE Threshold Edge Cases
# ---------------------------------------------------------------------------

class TestDTEThresholdEdgeCases:
    def test_exactly_at_threshold(self):
        """DTE exactly at threshold → force-close."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        # 24 hours exactly
        leg = make_short_leg(leg_id="exact-threshold", expiration_hours_from_now=24.0)
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert len(orders) == 1

    def test_one_minute_over_threshold(self):
        """DTE just over threshold → no order."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        # 24 hours + 1 minute
        leg = make_short_leg(
            leg_id="just-over",
            expiration_hours_from_now=24.0167,  # 24h + 1min
        )
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert orders == []

    def test_very_urgent(self):
        """DTE = 1 hour → close order with DTE threshold reason."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="urgent", expiration_hours_from_now=1.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        assert orders[0].dte_hours == pytest.approx(1.0, rel=0.1)
        # Reason is DTE threshold (not "before_expiry" which is for expired legs)
        assert orders[0].reason == "force_close_DTE_threshold_24h"

    def test_close_order_has_idempotency_key(self):
        """Every CloseOrder has a unique idempotency key."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="idempotency-test", expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        assert len(orders) == 1
        key = orders[0].idempotency_key
        assert key.startswith("fc-idempotency-test-")
        assert len(key) > 20


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestCloseOrderSchema:
    def test_close_order_schema_version(self):
        """CloseOrder has schema_version."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="schema-test", expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())
        assert orders[0].schema_version is not None

    def test_close_order_fields(self):
        """CloseOrder has all required fields."""
        guard = ForceCloseGuard(dte_threshold_hours=24)
        leg = make_short_leg(leg_id="fields-test", expiration_hours_from_now=20.0)
        orders = guard.assess(open_legs=[leg], now=make_now())

        o = orders[0]
        assert o.leg_id == "fields-test"
        assert o.symbol is not None
        assert o.action in ("BUY_TO_CLOSE", "SELL_TO_CLOSE")
        assert o.quantity > 0
        assert o.limit_price > 0
        assert o.expiration is not None
        assert o.idempotency_key is not None
        assert o.reason is not None
