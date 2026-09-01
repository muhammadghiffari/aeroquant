"""
execution/scheduler.py — Force-close polling scheduler and broker state reconciliation.

PRD §9: mandatory force-close before expiry.
PRD §5.4: four-plane architecture: execution plane holds scheduler.

Design:
  - Polling scheduler calls run_once() on a timer.
  - run_once() is deterministic and idempotent.
  - State is held in BrokerPositionTracker, persisted by the caller.
  - Tests mock AlpacaBroker entirely — no network.

BROKER-CONFIRMED STATE IS AUTHORITATIVE.

Each Alpaca account has its own scheduler and position tracker, scoped by account_id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution.broker import AlpacaBroker, BrokerPosition, BrokerOrder

logger = logging.getLogger("aeroquant.scheduler")


# ---------------------------------------------------------------------------
# Position / Order tracker (broker-confirmed state)
# ---------------------------------------------------------------------------

@dataclass
class BrokerPositionSnapshot:
    """
    Tracked open option position (or position-less leg). Persisted by the orchestrator.

    For reconciliation: the orchestrator builds these from BrokerReconcileResult.
    """

    leg_id: str
    symbol: str
    position_type: str  # "call" | "put"
    strike: float
    expiration: datetime
    quantity: int           # signed: negative = short
    status: str            # "open" | "closing" | "closed" | "expired" | "confirmed_closed"
    close_order_id: str | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    bid: float | None = None
    ask: float | None = None
    mark_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class BrokerReconcileResult:
    """
    Result of broker reconciliation. Returned by the tracker so the orchestrator
    can log / persist account-scoped state.
    """

    snapshot: list[BrokerPositionSnapshot]
    open_order_ids: set[str]
    newly_confirmed_legs: list[str]   # leg_ids confirmed filled since last poll
    newly_closed_orders: list[str]    # order_ids that disappeared from open orders
    newly_open_legs: list[str]      # legs that appeared new
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker_snapshot_available: bool = True
    broker_error: str | None = None


class BrokerPositionTracker:
    """
    Maintains broker-authoritative position state.
    Orchestrator persists this (e.g. SQLite / JSON per account).
    Scheduler reconstructs it from broker on startup.

    Idempotent: re-running reconciliation with same broker state is safe.
    """

    def __init__(self) -> None:
        self._positions: dict[str, BrokerPositionSnapshot] = {}   # leg_id -> snapshot
        self._open_orders: dict[str, str] = {}             # order_id -> leg_id (for tracking)
        self._log = logger

    # ---- Read API ----

    def get_open_legs(self) -> list[BrokerPositionSnapshot]:
        return [
            p for p in self._positions.values()
            if p.status in ("open", "closing")
        ]

    def is_leg_open(self, leg_id: str) -> bool:
        p = self._positions.get(leg_id)
        return p is not None and p.status in ("open", "closing")

    def is_order_open(self, order_id: str) -> bool:
        return order_id in self._open_orders

    def get_order_for_leg(self, leg_id: str) -> str | None:
        for oid, lid in self._open_orders.items():
            if lid == leg_id:
                return oid
        return None

    def is_leg_claimed(self, leg_id: str) -> bool:
        p = self._positions.get(leg_id)
        return p is not None and p.claimed_by is not None

    def get_short_legs_for_force_close(self):
        """Adapt broker-confirmed short positions for the deterministic guard."""
        from execution.force_close_guard import PositionStatus, ShortLegPosition

        return [
            ShortLegPosition(
                leg_id=position.leg_id,
                symbol=position.symbol,
                position_type=position.position_type,
                strike=position.strike,
                expiration=position.expiration,
                quantity=position.quantity,
                status=(
                    PositionStatus.CLOSING
                    if position.status == "closing"
                    else PositionStatus.OPEN
                ),
                close_order_id=position.close_order_id,
                claimed_by=position.claimed_by,
                claimed_at=position.claimed_at,
                current_bid=position.bid,
                current_ask=position.ask,
                mark_price=position.mark_price,
            )
            for position in self.get_open_legs()
            if position.quantity < 0
        ]

    # ---- Write API ----

    def record_submitted(self, leg_id: str, order_id: str, claimed_by: str) -> None:
        """Record that a close order was submitted to broker."""
        p = self._positions.get(leg_id)
        if p:
            p.close_order_id = order_id
            p.claimed_by = claimed_by
            p.claimed_at = datetime.now(timezone.utc)
            p.status = "closing"
        self._open_orders[order_id] = leg_id

    def record_filled(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: int,
        now: datetime,
    ) -> BrokerPositionSnapshot | None:
        """
        Record broker-confirmed fill. Returns the leg snapshot if found.
        Clears the open order and marks the leg closed.
        """
        leg_id = self._open_orders.get(order_id)
        if order_id in self._open_orders:
            del self._open_orders[order_id]
        if leg_id:
            p = self._positions.get(leg_id)
            if p:
                p.status = "confirmed_closed"
                p.close_order_id = None
                self._log.info("tracked_filled leg=%s order=%s fill_price=%.4f", leg_id, order_id, fill_price)
                return p
        return None

    def record_cancelled(
        self,
        order_id: str,
        reason: str,
        now: datetime,
    ) -> None:
        """Record broker-confirmed cancellation. Clears the open order."""
        leg_id = self._open_orders.get(order_id)
        if order_id in self._open_orders:
            del self._open_orders[order_id]
        if leg_id:
            p = self._positions.get(leg_id)
            if p and p.status == "closing":
                p.status = "open"
                p.close_order_id = None
                p.claimed_by = None
                p.claimed_by = None

    # ---- Reconciliation ----

    def reconcile(
        self,
        broker_positions: list | None,
        open_order_ids: set[str] | None,
        now: datetime,
        broker_error: str | None = None,
    ) -> BrokerReconcileResult:
        """Reconcile a successful broker snapshot, or retain state on uncertainty."""
        if broker_positions is None:
            self._log.warning("reconcile_snapshot_unavailable error=%s", broker_error)
            return BrokerReconcileResult(
                snapshot=list(self._positions.values()),
                open_order_ids=set(self._open_orders),
                newly_confirmed_legs=[],
                newly_closed_orders=[],
                newly_open_legs=[],
                timestamp=now,
                broker_snapshot_available=False,
                broker_error=broker_error,
            )

        broker_by_leg_id = {
            _broker_position_leg_id(position): position
            for position in broker_positions
        }
        broker_leg_ids = set(broker_by_leg_id)
        tracker_leg_ids = set(self._positions)
        tracker_order_ids = set(self._open_orders)
        broker_open_order_ids = None if open_order_ids is None else set(open_order_ids)

        # Diffs are sorted so reconciliation is deterministic for persistence.
        newly_open = sorted(broker_leg_ids - tracker_leg_ids)
        newly_closed_orders = (
            []
            if broker_open_order_ids is None
            else sorted(tracker_order_ids - broker_open_order_ids)
        )

        # Preserve the order-to-leg mapping before removing disappeared orders.
        disappeared_orders = {
            order_id: self._open_orders[order_id]
            for order_id in newly_closed_orders
        }
        for order_id in newly_closed_orders:
            self._log.debug("reconcile_order_gone order_id=%s", order_id)
            del self._open_orders[order_id]

        # Add only broker-confirmed new positions with their canonical tracker ID.
        for leg_id in newly_open:
            self._positions[leg_id] = _broker_pos_to_snapshot(broker_by_leg_id[leg_id])

        # Broker absence is the confirmation that a tracked leg is no longer open.
        newly_confirmed_legs: list[str] = []
        for leg_id in sorted(tracker_leg_ids - broker_leg_ids):
            position = self._positions[leg_id]
            position.status = "confirmed_closed"
            position.close_order_id = None
            newly_confirmed_legs.append(leg_id)

        # A disappeared close order while its broker position remains open was not
        # filled; release its local claim for a subsequent force-close attempt.
        for leg_id in disappeared_orders.values():
            if leg_id in broker_leg_ids:
                position = self._positions.get(leg_id)
                if position and position.status == "closing":
                    position.status = "open"
                    position.close_order_id = None
                    position.claimed_by = None
                    position.claimed_at = None

        return BrokerReconcileResult(
            snapshot=list(self._positions.values()),
            open_order_ids=set(self._open_orders),
            newly_confirmed_legs=newly_confirmed_legs,
            newly_closed_orders=newly_closed_orders,
            newly_open_legs=newly_open,
            timestamp=now,
            broker_snapshot_available=True,
        )

    def reconcile_from_broker(self, broker, now: datetime) -> BrokerReconcileResult:
        """Fetch one broker snapshot and fail closed if it cannot be obtained."""
        try:
            snapshot = broker.reconcile()
        except Exception as exc:  # noqa: BLE001 -- external state unavailable
            return self.reconcile(
                broker_positions=None,
                open_order_ids=None,
                now=now,
                broker_error=str(exc),
            )
        return self.reconcile(
            broker_positions=snapshot.get("positions"),
            open_order_ids=snapshot.get("open_order_ids"),
            now=now,
        )


@dataclass
class ForceCloseExecutionResult:
    """One deterministic force-close pass; submission is never a fill confirmation."""

    reconciliation: BrokerReconcileResult
    close_orders: list
    dispatch_results: list


class ForceCloseExecutor:
    """Wire broker snapshot → tracker → guard → typed close dispatcher once."""

    def __init__(self, tracker, guard, close_dispatcher) -> None:
        self._tracker = tracker
        self._guard = guard
        self._close_dispatcher = close_dispatcher

    def run_once(self, broker, now: datetime) -> ForceCloseExecutionResult:
        reconciliation = self._tracker.reconcile_from_broker(broker, now)
        if not reconciliation.broker_snapshot_available:
            return ForceCloseExecutionResult(reconciliation, [], [])

        close_orders = self._guard.assess(
            self._tracker.get_short_legs_for_force_close(), now
        )
        dispatch_results = [
            self._close_dispatcher.dispatch(close_order)
            for close_order in close_orders
        ]
        return ForceCloseExecutionResult(reconciliation, close_orders, dispatch_results)

    def record_broker_confirmed_fill(
        self, order_id: str, fill_price: float, fill_qty: int, now: datetime
    ) -> BrokerPositionSnapshot | None:
        """Advance close state only after an explicit broker fill confirmation."""
        position = self._tracker.record_filled(order_id, fill_price, fill_qty, now)
        if position is not None:
            self._guard.on_close_confirmed(position.leg_id, order_id, fill_price, now)
        return position


def _broker_pos_to_snapshot(p) -> BrokerPositionSnapshot:
    """Convert broker-position raw type to tracked snapshot fields."""
    if isinstance(p, BrokerPositionSnapshot):
        return replace(p)
    qty = getattr(p, "quantity", 0) or 0
    expiration = getattr(p, "expiration", None)
    position_type = getattr(p, "position_type", None)
    if expiration is None or position_type not in ("call", "put"):
        raise ValueError("Broker position is not a parseable option contract")
    return BrokerPositionSnapshot(
        leg_id=_broker_position_leg_id(p),
        symbol=getattr(p, "symbol", "?"),
        position_type=position_type,
        strike=float(getattr(p, "strike", 0) or 0),
        expiration=expiration,
        quantity=qty,
        status="open" if qty != 0 else "closed",
        unrealized_pl=float(getattr(p, "unrealized_pl", 0) or 0),
        realized_pnl=float(getattr(p, "realized_pnl", 0) or 0),
        bid=getattr(p, "current_bid", None),
        ask=getattr(p, "current_ask", None),
        mark_price=getattr(p, "mark_price", None),
    )


def _broker_position_leg_id(position) -> str:
    """Use the broker/tracker canonical leg ID, falling back to symbol."""
    return getattr(position, "leg_id", None) or getattr(position, "symbol", "?")
