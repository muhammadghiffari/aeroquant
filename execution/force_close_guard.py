"""
execution/force_close_guard.py — Mandatory force-close-before-expiry guard.

PRD §9: Confirmed paper-settlement bug (~$9,700 error on a $100k account).
MANDATORY: force-close every short leg before expiry. Never let a position
settle naturally.

DESIGN
------
  - Scheduled job (runs every N minutes via cron or scheduler)
  - Reads open positions from broker state (not LLM memory)
  - Identifies short legs with DTE <= threshold
  - Submits explicit close orders via Alpaca trading API
  - Idempotent: if a close order is already submitted/filled, do nothing
  - Logs everything for audit
  - Marks positions as claimed_by="force_close" to prevent race conditions

BROKER STATE CONTRACT
--------------------
  The execution layer maintains a local view of broker positions.
  This module does NOT call Alpaca directly — it consumes a position snapshot
  passed by the orchestrator's broker monitor. This keeps the module testable
  without network access.

  The orchestrator's broker monitor is responsible for:
    1. Fetching open positions from Alpaca
    2. Calling ForceCloseGuard.assess() with the snapshot
    3. Dispatching any CloseOrders returned by assess()
    4. Confirming fills via Alpaca's order API

COMPATIBLE WITH BROKER-CONFIRMED CLOSE STATE
-------------------------------------------
  assess() returns CloseOrders. The orchestrator submits them.
  On fill confirmation, the orchestrator marks the position as closed.
  If a position was already closed (by TP/SL, manual close, or force-close),
  it won't appear in the next snapshot and assess() will not return a CloseOrder.

  on_close_confirmed() is called by the orchestrator when a CloseOrder is
  filled, so the guard can update its internal claimed set and log the outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("aeroquant.force_close_guard")

CLAIMED_BY_FORCE_CLOSE = "force_close"
SCHEMA_VERSION = "2.6.0"

# Default: close any short leg with DTE <= 1 calendar day before its expiry
# PRD §9 says "before expiry" — 1-day DTE gives one full trading session to close
DEFAULT_DTE_THRESHOLD = 1


class PositionStatus(str, Enum):
    """Lifecycle status of a position."""

    OPEN = "open"
    CLOSING = "closing"       # Close order submitted, awaiting fill
    CLOSED = "closed"         # Confirmed fill
    EXPIRED = "expired"       # NATURAL EXPIRY — never allowed, log as incident
    CLOSE_CONFIRMED = "close_confirmed"


@dataclass
class ShortLegPosition:
    """
    A short option leg requiring force-close monitoring.

    Passed by the orchestrator's broker monitor to ForceCloseGuard.assess().
    """

    leg_id: str                   # Unique identifier for this leg
    symbol: str                   # Contract symbol
    position_type: Literal["call", "put"]
    strike: float
    expiration: datetime           # Contract expiration (UTC)
    quantity: int                  # Short quantity (negative = short)

    # State fields
    status: PositionStatus = PositionStatus.OPEN
    close_order_id: str | None = None   # Alpaca order ID if close submitted
    claimed_by: str | None = None       # Who submitted the close order
    claimed_at: datetime | None = None

    # Pricing (for limit order)
    current_bid: float | None = None
    current_ask: float | None = None
    mark_price: float | None = None


class CloseOrder(BaseModel):
    """
    An explicit close order returned by ForceCloseGuard.assess().

    The orchestrator submits this via Alpaca's trading API.
    Idempotency: CloseOrder.close_leg_id is used as the idempotency key.
    """

    model_config = {"str_strip_whitespace": True}

    schema_version: str = Field(default=SCHEMA_VERSION)
    leg_id: str = Field(description="ShortLegPosition.leg_id being closed")
    symbol: str = Field(description="Contract symbol")
    action: Literal["BUY_TO_CLOSE", "SELL_TO_CLOSE"] = Field(
        description="BUY_TO_CLOSE for short call/put; SELL_TO_CLOSE for long"
    )
    quantity: int = Field(ge=1, description="Contracts to close")

    # Limit price: pad 5% toward adverse side per PRD §7
    limit_price: float = Field(
        description="Adjusted limit price for the close order. "
                    "BUY_TO_CLOSE: pad ask UP 5%. "
                    "SELL_TO_CLOSE: pad bid DOWN 5%."
    )

    expiration: datetime = Field(
        description="Close order expiry (UTC). Should be today or very short-dated."
    )
    idempotency_key: str = Field(
        description="Unique key for idempotent submission: f'fc-{leg_id}-{timestamp}'"
    )
    reason: str = Field(
        description="Human-readable reason: 'force_close_before_expiry' or 'force_close_DTE_threshold'"
    )
    dte_hours: float = Field(
        description="Hours remaining until contract expiry at time of assessment"
    )


@dataclass
class ForceCloseJob:
    """
    Scheduled job record produced by assess().

    The orchestrator persists this and runs the actual close orders.
    On completion (fill or cancellation), on_close_confirmed() is called.
    """

    job_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    close_orders: list[CloseOrder] = field(default_factory=list)
    status: Literal["pending", "submitted", "completed", "partial", "failed"] = "pending"
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


class ForceCloseGuard:
    """
    Deterministic force-close guard.

    Designed for:
      - Testable without network access
      - Idempotent (safe to call multiple times)
      - Audit-friendly (logs every assessment)
      - Compatible with broker-confirmed state

    Usage:
      guard = ForceCloseGuard(dte_threshold_hours=24)
      orders = guard.assess(
          open_legs=[...],           # List[ShortLegPosition] from broker snapshot
          now=datetime.now(timezone.utc),
      )
      # orchestrator submits CloseOrders, then calls guard.on_close_confirmed(...)
    """

    def __init__(
        self,
        dte_threshold_hours: int = DEFAULT_DTE_THRESHOLD * 24,  # Default: 24h = 1 calendar day
        pad_pct: float = 0.05,  # 5% limit price pad per PRD §7
    ) -> None:
        self.dte_threshold_hours = dte_threshold_hours
        self.pad_pct = pad_pct

        # Internal state: legs currently claimed by an outstanding close order
        self._claimed_legs: dict[str, datetime] = {}

    def assess(
        self,
        open_legs: list[ShortLegPosition],
        now: datetime,
    ) -> list[CloseOrder]:
        """
        Main entry point. Returns a list of CloseOrders to submit.

        Args:
            open_legs: Current snapshot of short legs from broker.
            now: Current time (UTC).

        Returns:
            List of CloseOrders to submit. Empty = nothing to force-close.

        Logic:
          For each open short leg:
            1. If DTE <= threshold: force-close (PRD §9)
            2. If already claimed by force-close: skip (idempotent)
            3. If close order already submitted (status=CLOSING): skip
            4. If expired natural: LOG INCIDENT (PRD §9 violation — should never happen)
        """
        orders: list[CloseOrder] = []

        for leg in open_legs:
            dte_hours = self._hours_until_expiry(leg.expiration, now)

            # --- Check for natural expiry violation ---
            if dte_hours <= 0:
                if leg.status == PositionStatus.OPEN:
                    logger.critical(
                        "FORCE_CLOSE_VIOLATION leg=%s symbol=%s expired_at=%s "
                        "Natural expiry detected — PRD §9 breach! "
                        "Immediate manual intervention required.",
                        leg.leg_id, leg.symbol, leg.expiration.isoformat()
                    )
                    # Don't create a close order for an already-expired leg
                    continue
                else:
                    # Already closing or closed — normal
                    continue

            # --- Already claimed by force-close ---
            if leg.leg_id in self._claimed_legs:
                logger.debug(
                    "force_close_skip_already_claimed leg=%s claimed_at=%s",
                    leg.leg_id, self._claimed_legs[leg.leg_id].isoformat()
                )
                continue

            # --- Already has a close order submitted (from TP/SL or manual) ---
            if leg.status == PositionStatus.CLOSING and leg.close_order_id:
                logger.debug(
                    "force_close_skip_already_closing leg=%s order_id=%s",
                    leg.leg_id, leg.close_order_id
                )
                continue

            # --- Already closed ---
            if leg.status in (PositionStatus.CLOSED, PositionStatus.CLOSE_CONFIRMED):
                continue

            # --- DTE threshold check ---
            if dte_hours <= self.dte_threshold_hours:
                close_order = self._create_close_order(leg, now, dte_hours)
                orders.append(close_order)
                self._claimed_legs[leg.leg_id] = now

                logger.info(
                    "force_close_order_created leg=%s symbol=%s dte_hours=%.1f "
                    "threshold_hours=%d limit_price=%.4f reason=%s",
                    leg.leg_id, leg.symbol, dte_hours, self.dte_threshold_hours,
                    close_order.limit_price, close_order.reason
                )

        if orders:
            logger.info(
                "force_close_assessment created=%d orders for %d open legs",
                len(orders), len(open_legs)
            )

        return orders

    def on_close_confirmed(
        self,
        leg_id: str,
        order_id: str,
        fill_price: float,
        now: datetime,
    ) -> None:
        """
        Called by the orchestrator when a force-close order is filled.

        Removes the leg from the claimed set and logs the outcome.
        """
        if leg_id in self._claimed_legs:
            del self._claimed_legs[leg_id]
            logger.info(
                "force_close_confirmed leg=%s order_id=%s fill_price=%.4f",
                leg_id, order_id, fill_price
            )
        else:
            logger.warning(
                "force_close_confirmed unexpected_leg leg_id=%s order_id=%s "
                "(leg not in claimed set — may have been double-closed)",
                leg_id, order_id
            )

    def on_close_cancelled(
        self,
        leg_id: str,
        reason: str,
        now: datetime,
    ) -> None:
        """
        Called by the orchestrator when a force-close order is cancelled or rejected.

        Releases the claim so assess() can try again on next cycle.
        """
        if leg_id in self._claimed_legs:
            del self._claimed_legs[leg_id]
            logger.warning(
                "force_close_cancelled leg=%s reason=%s — will retry next cycle",
                leg_id, reason
            )

    def get_claimed_legs(self) -> dict[str, datetime]:
        """Return the current claimed-leg set (for orchestrator audit)."""
        return dict(self._claimed_legs)

    def _hours_until_expiry(self, expiry: datetime, now: datetime) -> float:
        """Calendar hours until expiry. Positive = still alive."""
        delta = expiry - now
        return delta.total_seconds() / 3600.0

    def _create_close_order(
        self,
        leg: ShortLegPosition,
        now: datetime,
        dte_hours: float,
    ) -> CloseOrder:
        """
        Build a CloseOrder for a short leg.

        Limit price: pad 5% toward adverse side per PRD §7.
        BUY_TO_CLOSE: pad ask UP 5% (we're buying to close a short)
        SELL_TO_CLOSE: pad bid DOWN 5% (we're selling to close a long)
        """
        is_short = leg.quantity < 0
        action: Literal["BUY_TO_CLOSE", "SELL_TO_CLOSE"] = (
            "BUY_TO_CLOSE" if is_short else "SELL_TO_CLOSE"
        )

        if leg.current_bid is not None and leg.current_ask is not None:
            if action == "BUY_TO_CLOSE":
                # Adverse side is ask (we're buying)
                base_price = leg.current_ask
                limit = base_price * (1 + self.pad_pct)
            else:
                # Adverse side is bid (we're selling)
                base_price = leg.current_bid
                limit = base_price * (1 - self.pad_pct)
        elif leg.mark_price is not None:
            # Fallback to mark
            limit = leg.mark_price * (1 + self.pad_pct)
        else:
            # Last resort: use mark_price or 0 — order will likely not fill
            limit = 0.01
            logger.warning(
                "force_close_no_price_data leg=%s symbol=%s — using emergency limit price",
                leg.leg_id, leg.symbol
            )

        # Expiration: close order expires at end of today (UTC)
        # This ensures it doesn't accidentally persist into the next session
        close_expires = now.replace(hour=23, minute=59, second=59, microsecond=59)
        if close_expires < now:
            close_expires = close_expires + timedelta(days=1)

        reason = (
            "force_close_before_expiry"
            if dte_hours <= 0
            else f"force_close_DTE_threshold_{self.dte_threshold_hours}h"
        )

        return CloseOrder(
            leg_id=leg.leg_id,
            symbol=leg.symbol,
            action=action,
            quantity=abs(leg.quantity),
            limit_price=round(limit, 4),
            expiration=close_expires,
            idempotency_key=f"fc-{leg.leg_id}-{now.strftime('%Y%m%d%H%M%S')}",
            reason=reason,
            dte_hours=round(dte_hours, 2),
        )
