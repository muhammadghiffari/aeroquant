"""
execution/order_dispatcher.py — Converts RiskDecision → broker order submission.

PRD §7 / CLAUDE.md:
  Multi-leg orders only (MLEG, atomic fill — no legging in).
  Limit orders only (no market orders for options).
  Pad ±5% toward adverse side (already done in RiskManager._build_order_intent).

ARCHITECTURE RULE:
  LLM agents may PROPOSE.  Only this dispatcher calls the broker.
  The dispatcher REQUIRES a RiskDecision with status APPROVED.
  A raw OrderIntent without a RiskDecision wrapper is rejected at the type level.
  No LLM has a broker client or order submission path.

IDEMPOTENCY:
  Every submission uses the RiskDecision.order_intent.idempotency_key.
  The broker (Alpaca) is queried before submission to detect already-submitted orders.
  Safe to call on restart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from math import gcd

from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    OrderType,
    PositionIntent,
    TimeInForce,
)
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

from agents.risk_manager import RiskDecision, RiskStatus
from execution.force_close_guard import CLAIMED_BY_FORCE_CLOSE, CloseOrder

logger = logging.getLogger("aeroquant.dispatcher")

# Cannot be forward-declared cleanly in dataclass defaults, so resolved inline.


@dataclass
class DispatchResult:
    """Outcome of a dispatch attempt."""

    decision: RiskDecision
    broker_order_id: str | None = None   # set on broker confirmation
    rejected_reason: str | None = None  # set when unapproved
    dispatched: bool = False
    idempotent_skip: bool = False       # True = already-submitted (idempotent)
    error: str | None = None


class OrderDispatcher:
    """
    Deterministic order dispatcher.

    Receives a RiskDecision (from the Risk Gate) and submits to the broker.
    Enforces that APPROVED decisions only flow to the broker.
    Rejects anything without a valid RiskDecision.

    Idempotent: re-calling with the same RiskDecision (same proposal_id) within
    the same session is safe.  On restart the orchestrator calls reconcile().

    Params:
      broker: AlpacaBroker concrete instance (paper trading only for now).
      clock_poll_seconds: how long to wait for broker confirmation.
    """

    def __init__(
        self,
        broker,  # type: AlpacaBroker
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = logger
        # Broker-confirmed order IDs in this dispatcher lifetime. The broker still
        # receives the idempotency key, which remains the duplicate guard after a
        # restart; this map prevents duplicate submission in this process.
        self._submitted_order_ids: dict[str, str] = {}

    def dispatch(self, decision: RiskDecision) -> DispatchResult:
        """
        Main entry point. Submit an approved order to the broker.

        Args:
            decision: RiskDecision from RiskManager.evaluate(). Must be APPROVED.

        Returns DispatchResult.  broker is called on APPROVED; no-op on REJECTED.

        The broker's order_id is returned in DispatchResult.broker_order_id on success.
        """
        # --- AUTHORIZATION GATE: only APPROVED decisions reach the broker ---
        if not isinstance(decision, RiskDecision):
            raise TypeError("OrderDispatcher requires a RiskDecision authorization wrapper")
        if decision.decision != RiskStatus.APPROVED:
            self._log.warning(
                "dispatch_rejected_not_approved proposal=%s status=%s reasons=%s",
                decision.proposal_id, decision.decision.value, decision.reasons
            )
            return DispatchResult(
                decision=decision,
                rejected_reason=f"Decision is {decision.decision.value}, not APPROVED",
            )

        intent = decision.order_intent
        if intent is None:
            self._log.error(
                "dispatch_rejected_no_intent proposal=%s",
                decision.proposal_id
            )
            return DispatchResult(
                decision=decision,
                rejected_reason="APPROVED decision has no OrderIntent — broker submission blocked",
            )

        # The OrderIntent and broker instance are both account-scoped.
        if intent.account_id != self._broker.account_id:
            self._log.error(
                "dispatch_rejected_account_mismatch proposal=%s intent_account=%s broker_account=%s",
                decision.proposal_id, intent.account_id, self._broker.account_id,
            )
            return DispatchResult(
                decision=decision,
                rejected_reason="OrderIntent account does not match broker account — broker submission blocked",
            )

        # Defend the limit-only contract at the side-effect boundary. Literal
        # validation normally rejects this, but an unvalidated model copy must
        # never send a market option order to the broker.
        if intent.order_type != "limit":
            self._log.error(
                "dispatch_rejected_non_limit proposal=%s order_type=%s",
                decision.proposal_id, intent.order_type,
            )
            return DispatchResult(
                decision=decision,
                rejected_reason="Options orders must be LIMIT — broker submission blocked",
            )

        now = self._clock()
        if intent.expires_at.tzinfo is None or now >= intent.expires_at:
            return DispatchResult(
                decision=decision,
                rejected_reason="OrderIntent has expired — broker submission blocked",
            )

        # --- RECONCILE: broker-authoritative idempotency check ---
        try:
            existing = self._find_existing_order(intent.idempotency_key)
        except Exception as exc:  # noqa: BLE001
            self._log.error("dispatch_idempotency_lookup_error proposal=%s error=%s", decision.proposal_id, exc)
            return DispatchResult(
                decision=decision,
                error=f"Broker idempotency lookup failed: {exc}",
            )
        if existing is not None:
            self._log.info(
                "dispatch_idempotent_skip proposal=%s order_id=%s",
                decision.proposal_id, existing
            )
            return DispatchResult(
                decision=decision,
                broker_order_id=existing,
                dispatched=False,
                idempotent_skip=True,
            )

        # --- BUILD one typed, atomic MLEG request ---
        try:
            order_payload = _build_mleg_payload(intent)
        except ValueError as exc:
            return DispatchResult(
                decision=decision,
                rejected_reason=f"Invalid MLEG intent — broker submission blocked: {exc}",
            )
        self._log.info(
            "dispatch_submitting proposal=%s intent_id=%s legs=%d",
            decision.proposal_id, intent.intent_id, len(intent.legs)
        )

        # --- SUBMIT ---
        try:
            broker_order_id = self._broker.submit_order(
                order_data=order_payload,
                idempotency_key=intent.idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "dispatch_broker_error proposal=%s error=%s",
                decision.proposal_id, exc
            )
            return DispatchResult(
                decision=decision,
                error=str(exc),
            )

        self._log.info(
            "dispatch_submitted proposal=%s broker_order_id=%s",
            decision.proposal_id, broker_order_id
        )
        self._submitted_order_ids[intent.idempotency_key] = broker_order_id

        return DispatchResult(
            decision=decision,
            broker_order_id=broker_order_id,
            dispatched=True,
        )

    def _find_existing_order(self, idempotency_key: str) -> str | None:
        """
        Check if an order with the given idempotency key is already open/filled.
        Returns broker order_id if found, None if not.
       alpaca-idempotency key is checked by querying open orders on the account.
        """
        # The account-scoped broker lookup is authoritative and works after
        # restart. Its errors intentionally propagate: uncertainty must not be
        # converted into permission to submit a potentially duplicate order.
        existing = self._broker.find_order_by_client_order_id(idempotency_key)
        if existing is not None:
            self._submitted_order_ids[idempotency_key] = existing
            return existing
        return None


# ---------------------------------------------------------------------------
# MLEG order builder
# ---------------------------------------------------------------------------

def _build_mleg_payload(intent) -> LimitOrderRequest:
    """Build one typed, atomic Alpaca MLEG limit-order request.

    Alpaca's MLEG contract puts the spread quantity and limit price on the
    parent request. Each option leg contributes a ratio, side, and opening
    position intent; it does not carry a per-leg limit price or quantity.
    """
    if not intent.mleg_atomic:
        raise ValueError("Non-MLEG orders are not supported.")
    if intent.order_type != "limit":
        raise ValueError("Options orders must be LIMIT only.")
    if len(intent.legs) < 2:
        raise ValueError("MLEG orders require at least two legs.")

    order_quantity = _mleg_order_quantity(intent)
    legs = [
        OptionLegRequest(
            symbol=leg.symbol,
            ratio_qty=leg.quantity // order_quantity,
            side=_order_side(leg.action),
            position_intent=_opening_position_intent(leg.action),
        )
        for leg in intent.legs
    ]
    return LimitOrderRequest(
        qty=order_quantity,
        type=OrderType.LIMIT,
        limit_price=_spread_aggregate_limit(intent),
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.MLEG,
        legs=legs,
    )


def _mleg_order_quantity(intent) -> int:
    """Find the parent quantity that yields integral per-leg MLEG ratios."""
    quantity = intent.legs[0].quantity
    for leg in intent.legs[1:]:
        quantity = gcd(quantity, leg.quantity)
    return quantity


def _order_side(action: str) -> OrderSide:
    return OrderSide.BUY if action == "BUY" else OrderSide.SELL


def _opening_position_intent(action: str) -> PositionIntent:
    return (
        PositionIntent.BUY_TO_OPEN
        if action == "BUY"
        else PositionIntent.SELL_TO_OPEN
    )


def _spread_aggregate_limit(intent) -> float:
    """
    Aggregate limit price for the MLEG spread.

    The signed parent limit is calculated per MLEG unit:
    sum(buy_leg_price × ratio) - sum(sell_leg_price × ratio).
    Positive = net debit (we pay); negative = net credit (we receive).
    Alpaca derives opening semantics from each leg's position intent, not the
    parent side.

    The formula includes ALL legs with their ratio quantities — never abs(),
    never derived from short legs alone.
    """
    order_quantity = _mleg_order_quantity(intent)
    signed_net_price = sum(
        (leg.limit_price if leg.action == "BUY" else -leg.limit_price)
        * (leg.quantity // order_quantity)
        for leg in intent.legs
    )
    return round(signed_net_price, 4)


@dataclass
class CloseDispatchResult:
    """Outcome of deterministic explicit-close submission; never a fill confirmation."""

    close_order: CloseOrder
    broker_order_id: str | None = None
    dispatched: bool = False
    idempotent_skip: bool = False
    rejected_reason: str | None = None
    error: str | None = None


class CloseOrderDispatcher:
    """Translate a deterministic ForceCloseGuard CloseOrder into one typed limit request."""

    def __init__(self, broker, tracker=None, clock: Callable[[], datetime] | None = None) -> None:
        self._broker = broker
        self._tracker = tracker
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def dispatch(self, close_order: CloseOrder) -> CloseDispatchResult:
        now = self._clock()
        if close_order.expiration.tzinfo is None or now >= close_order.expiration:
            return CloseDispatchResult(
                close_order=close_order,
                rejected_reason="CloseOrder has expired — broker submission blocked",
            )
        try:
            existing = self._broker.find_order_by_client_order_id(close_order.idempotency_key)
        except Exception as exc:  # noqa: BLE001
            return CloseDispatchResult(close_order=close_order, error=f"Broker idempotency lookup failed: {exc}")
        if existing is not None:
            return CloseDispatchResult(
                close_order=close_order, broker_order_id=existing, idempotent_skip=True
            )

        request = _build_close_limit_request(close_order)
        try:
            broker_order_id = self._broker.submit_order(
                order_data=request, idempotency_key=close_order.idempotency_key
            )
        except Exception as exc:  # noqa: BLE001
            return CloseDispatchResult(close_order=close_order, error=str(exc))

        # An accepted order is tracked as closing, never as filled or closed.
        if self._tracker is not None:
            self._tracker.record_submitted(
                close_order.leg_id, broker_order_id, CLAIMED_BY_FORCE_CLOSE
            )
        return CloseDispatchResult(
            close_order=close_order, broker_order_id=broker_order_id, dispatched=True
        )


def _build_close_limit_request(close_order: CloseOrder) -> LimitOrderRequest:
    """Build the typed explicit option-close request used by ForceCloseGuard."""
    if close_order.action == "BUY_TO_CLOSE":
        side = OrderSide.BUY
        position_intent = PositionIntent.BUY_TO_CLOSE
    elif close_order.action == "SELL_TO_CLOSE":
        side = OrderSide.SELL
        position_intent = PositionIntent.SELL_TO_CLOSE
    else:  # defensive boundary for an unvalidated CloseOrder mutation
        raise ValueError("Unsupported close action")
    return LimitOrderRequest(
        symbol=close_order.symbol,
        qty=close_order.quantity,
        side=side,
        type=OrderType.LIMIT,
        limit_price=close_order.limit_price,
        time_in_force=TimeInForce.DAY,
        position_intent=position_intent,
    )
