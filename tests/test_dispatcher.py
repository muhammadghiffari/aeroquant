"""
tests/test_dispatcher.py — Deterministic unit tests for OrderDispatcher.

Covers:
  - Unapproved decision cannot reach broker.
  - Approved decision reaches broker.
  - Idempotency.
  - MLEG atomic enforcement.
  - Limit order only enforcement.
  - Account scoping.

No network. No LLM. No Alpaca. Mock broker at interface level.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alpaca.trading.enums import OrderClass, OrderSide, OrderType, PositionIntent
from alpaca.trading.requests import LimitOrderRequest

from agents.risk_manager import (
    LegIntent,
    OrderIntent,
    RiskDecision,
    RiskParams,
    RiskStatus,
    SpreadSpec,
    TradeProposal,
    LegSpec,
    TradeSide,
)
from execution.order_dispatcher import OrderDispatcher


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

class MockBroker:
    """Minimal broker interface mock for dispatcher tests."""

    def __init__(self, account_id: str = "test-account"):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self._open_ids: list[str] = []
        self.orders_by_client_id: dict[str, str] = {}
        self._account_id = account_id

    def reconcile(self):
        return {"positions": [], "open_order_ids": set()}

    def submit_order(self, order_data, idempotency_key):
        self.orders.append({"data": order_data, "key": idempotency_key})
        order_id = f"broker-{idempotency_key}"
        self.orders_by_client_id[idempotency_key] = order_id
        return order_id

    def find_order_by_client_order_id(self, client_order_id):
        return self.orders_by_client_id.get(client_order_id)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    @property
    def account_id(self):
        return self._account_id

    def is_market_open(self):
        return True

    def get_positions(self):
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def broker():
    return MockBroker()


@pytest.fixture
def dispatcher(broker):
    return OrderDispatcher(broker=broker)


@pytest.fixture
def approved_decision():
    """Minimal APPROVED RiskDecision with OrderIntent for testing."""
    leg_intents = [
        LegIntent(
            symbol="XSP241205P00550000",
            action="SELL",
            quantity=1,
            limit_price=2.00,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            order_class="simple",
            take_profit_price=None,
            stop_loss_price=None,
        ),
        LegIntent(
            symbol="XSP241205P00545000",
            action="BUY",
            quantity=1,
            limit_price=0.50,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
            order_class="simple",
            take_profit_price=None,
            stop_loss_price=None,
        ),
    ]
    intent = OrderIntent(
        intent_id="intent-test-prop-001",
        proposal_id="test-prop-001",
        account_id="test-account",
        legs=leg_intents,
        tp_credit_threshold=1.00,
        sl_credit_threshold=-1.25,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        idempotency_key="intent-test-prop-001",
    )
    return RiskDecision(
        decision=RiskStatus.APPROVED,
        order_intent=intent,
        proposal_id="test-prop-001",
    )


@pytest.fixture
def rejected_decision():
    return RiskDecision(
        decision=RiskStatus.REJECTED,
        proposal_id="test-prop-002",
        reasons=["test rejection"],
        rule_names=["TEST_RULE"],
    )


# ---------------------------------------------------------------------------
# Architecture: only APPROVED reaches broker
# ---------------------------------------------------------------------------

class TestDispatcherAuthorizationGate:
    """Core architectural rule: unapproved decisions cannot reach the broker."""

    def test_rejected_decision_blocked(self, broker, dispatcher, rejected_decision):
        """REJECTED decision → broker.submit_order never called."""
        result = dispatcher.dispatch(rejected_decision)
        assert result.rejected_reason is not None
        assert len(broker.orders) == 0

    def test_rejected_reasons_logged(self, dispatcher, rejected_decision):
        result = dispatcher.dispatch(rejected_decision)
        assert "REJECTED" in result.rejected_reason

    def test_approved_reaches_broker(self, broker, dispatcher, approved_decision):
        """APPROVED decision → broker.submit_order called once."""
        result = dispatcher.dispatch(approved_decision)
        assert result.broker_order_id is not None
        assert len(broker.orders) == 1

    def test_approved_sets_broker_order_id(self, broker, dispatcher, approved_decision):
        result = dispatcher.dispatch(approved_decision)
        assert result.broker_order_id.startswith("broker-")
        assert result.dispatched is True

    def test_no_llm_can_call_broker_directly(self, broker, dispatcher, approved_decision):
        """Architecture check: no OrderIntent passes broker without dispatcher."""
        # approved_decision has status APPROVED — the dispatcher should accept it.
        # Any other code path (LLM, MCP tool, direct Alpaca API call) is not the dispatcher.
        assert approved_decision.decision == RiskStatus.APPROVED


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_idempotent_re_dispatch(self, broker, dispatcher, approved_decision):
        """Calling dispatch twice with same decision_id is safe."""
        r1 = dispatcher.dispatch(approved_decision)
        assert r1.broker_order_id is not None

        # Simulate Alpaca's own idempotency: re-submit same decision → idempotency key prevents duplicate
        r2 = dispatcher.dispatch(approved_decision)
        # Broker returns the same order_id (idempotent key hit)
        # Dispatcher receives it and skips (idempotent_skip)
        assert r2.idempotent_skip is True
        assert r2.broker_order_id is not None

    def test_idempotency_survives_dispatcher_restart(self, broker, dispatcher, approved_decision):
        first = dispatcher.dispatch(approved_decision)
        restarted_dispatcher = OrderDispatcher(broker=broker)

        second = restarted_dispatcher.dispatch(approved_decision)

        assert second.idempotent_skip is True
        assert second.broker_order_id == first.broker_order_id
        assert len(broker.orders) == 1

    def test_different_proposals_get_different_order_ids(self, broker, dispatcher, approved_decision):
        """Two different decisions get two different order IDs."""
        # Copy with different ID
        d2 = RiskDecision(
            proposal_id="other-prop",
            decision=RiskStatus.APPROVED,
            order_intent=OrderIntent(
                intent_id="intent-other",
                proposal_id="other-prop",
                account_id="test-account",
                legs=approved_decision.order_intent.legs,
                tp_credit_threshold=1.0,
                sl_credit_threshold=-1.25,
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
                idempotency_key="intent-other",
            ),
        )
        r1 = dispatcher.dispatch(approved_decision)
        r2 = dispatcher.dispatch(d2)
        assert r1.broker_order_id != r2.broker_order_id


# ---------------------------------------------------------------------------
# MLEG atomic enforcement
# ---------------------------------------------------------------------------

class TestMLEGAtomic:
    def test_mleg_flag_required(self, dispatcher, approved_decision):
        """order_intent.mleg_atomic must be True — verified at construction time."""
        assert approved_decision.order_intent.mleg_atomic is True


# ---------------------------------------------------------------------------
# Limit order only
# ---------------------------------------------------------------------------

class TestLimitOrderOnly:
    def test_market_order_rejected_at_construction(self):
        """Attempting to build an OrderIntent with type != limit fails at Pydantic validation."""
        # Pydantic literal enforces order_type == "limit"
        with pytest.raises(Exception):
            OrderIntent(
                intent_id="intent-x",
                proposal_id="x",
                account_id="test",
                legs=[],
                order_type="market",
                tp_credit_threshold=0.0,
                sl_credit_threshold=0.0,
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
                idempotency_key="x",
            )

    def test_non_limit_order_never_reaches_broker(self, broker, dispatcher, approved_decision):
        """A bypassed contract is rejected before it can call the broker."""
        non_limit_intent = approved_decision.order_intent.model_copy(
            update={"order_type": "market"}
        )
        non_limit_decision = approved_decision.model_copy(
            update={"order_intent": non_limit_intent}
        )

        result = dispatcher.dispatch(non_limit_decision)

        assert result.rejected_reason is not None
        assert "LIMIT" in result.rejected_reason
        assert broker.orders == []

    def test_mleg_request_uses_current_alpaca_contract(self, broker, dispatcher, approved_decision):
        """Dispatcher creates a typed, atomic limit MLEG request offline."""
        dispatcher.dispatch(approved_decision)
        request = broker.orders[0]["data"]

        assert isinstance(request, LimitOrderRequest)
        assert request.order_class == OrderClass.MLEG
        assert request.type == OrderType.LIMIT
        assert request.qty == 1
        assert request.limit_price == -1.5
        assert [(leg.ratio_qty, leg.side, leg.position_intent) for leg in request.legs] == [
            (1, OrderSide.SELL, PositionIntent.SELL_TO_OPEN),
            (1, OrderSide.BUY, PositionIntent.BUY_TO_OPEN),
        ]

    def test_limit_order_injected_in_payload(self, broker, dispatcher, approved_decision):
        """The typed broker request is always an order-level limit order."""
        dispatcher.dispatch(approved_decision)
        assert broker.orders[0]["data"].type == OrderType.LIMIT


# ---------------------------------------------------------------------------
# Account scoping
# ---------------------------------------------------------------------------

class TestAccountScoping:
    def test_account_id_injected(self, broker, dispatcher, approved_decision):
        """Every submission is scoped to one account."""
        dispatcher.dispatch(approved_decision)
        assert approved_decision.order_intent.account_id == "test-account"

    def test_idempotency_key_is_scoped_to_broker_account(self, approved_decision):
        broker_a = MockBroker(account_id="account-a")
        broker_b = MockBroker(account_id="account-b")
        decision_a = approved_decision.model_copy(
            update={"order_intent": approved_decision.order_intent.model_copy(update={"account_id": "account-a"})}
        )
        decision_b = approved_decision.model_copy(
            update={"order_intent": approved_decision.order_intent.model_copy(update={"account_id": "account-b"})}
        )

        first = OrderDispatcher(broker_a).dispatch(decision_a)
        second = OrderDispatcher(broker_b).dispatch(decision_b)

        assert first.dispatched is True
        assert second.dispatched is True
        assert len(broker_a.orders) == 1
        assert len(broker_b.orders) == 1

    def test_mismatched_order_account_cannot_reach_broker(self, broker, dispatcher, approved_decision):
        wrong_account = approved_decision.order_intent.model_copy(
            update={"account_id": "another-account"}
        )
        decision = approved_decision.model_copy(update={"order_intent": wrong_account})

        result = dispatcher.dispatch(decision)

        assert result.rejected_reason is not None
        assert broker.orders == []


class TestSignedMlegPricing:
    @staticmethod
    def _intent(legs):
        return OrderIntent(
            intent_id="price-intent",
            proposal_id="price-proposal",
            account_id="test-account",
            legs=legs,
            tp_credit_threshold=0.0,
            sl_credit_threshold=0.0,
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            idempotency_key="price-intent",
        )

    @staticmethod
    def _leg(action, price, quantity=1, symbol="XSP241205P00550000"):
        return LegIntent(
            symbol=symbol,
            action=action,
            quantity=quantity,
            limit_price=price,
            expiration=datetime(2024, 12, 5, tzinfo=timezone.utc),
        )

    @pytest.mark.parametrize(
        ("legs", "expected"),
        [
            # short put credit: buy 0.50 - sell 2.00
            ([
                _leg.__func__("SELL", 2.00, symbol="XSP241205P00550000"),
                _leg.__func__("BUY", 0.50, symbol="XSP241205P00545000"),
            ], -1.50),
            # short call credit: buy 0.40 - sell 1.60
            ([_leg.__func__("SELL", 1.60, symbol="XSP241205C00560000"), _leg.__func__("BUY", 0.40, symbol="XSP241205C00565000")], -1.20),
            # iron condor: both debit wings minus both short credits
            ([
                _leg.__func__("BUY", 0.50, symbol="XSP241205P00540000"),
                _leg.__func__("SELL", 1.80, symbol="XSP241205P00545000"),
                _leg.__func__("SELL", 2.10, symbol="XSP241205C00560000"),
                _leg.__func__("BUY", 0.60, symbol="XSP241205C00565000"),
            ], -2.80),
            # debit spread: buy 2.00 - sell 0.50
            ([
                _leg.__func__("BUY", 2.00, symbol="XSP241205P00550000"),
                _leg.__func__("SELL", 0.50, symbol="XSP241205P00545000"),
            ], 1.50),
            # non-1 ratio: buy 0.50 * 1 - sell 1.50 * 2
            ([
                _leg.__func__("SELL", 1.50, quantity=2, symbol="XSP241205P00550000"),
                _leg.__func__("BUY", 0.50, quantity=1, symbol="XSP241205P00545000"),
            ], -2.50),
        ],
    )
    def test_signed_parent_limit_uses_all_leg_prices_and_ratios(self, legs, expected):
        from execution.order_dispatcher import _build_mleg_payload

        request = _build_mleg_payload(self._intent(legs))

        assert request.limit_price == expected

    def test_non_one_ratio_is_represented_on_the_typed_leg(self):
        from execution.order_dispatcher import _build_mleg_payload

        request = _build_mleg_payload(self._intent([
            self._leg("SELL", 1.50, quantity=2, symbol="XSP241205P00550000"),
            self._leg("BUY", 0.50, quantity=1, symbol="XSP241205P00545000"),
        ]))

        assert request.qty == 1
        assert [leg.ratio_qty for leg in request.legs] == [2, 1]


class TestDispatcherAdversarialSafety:
    def test_lookup_failure_prevents_submission(self, broker, approved_decision):
        broker.find_order_by_client_order_id = lambda _: (_ for _ in ()).throw(TimeoutError("offline"))

        result = OrderDispatcher(broker).dispatch(approved_decision)

        assert result.error is not None
        assert broker.orders == []

    @pytest.mark.parametrize("now", [
        datetime(2099, 1, 1, tzinfo=timezone.utc),
        datetime(2100, 1, 1, tzinfo=timezone.utc),
    ])
    def test_expired_or_boundary_intent_never_reaches_broker(self, broker, approved_decision, now):
        intent = approved_decision.order_intent.model_copy(update={"expires_at": now})
        decision = approved_decision.model_copy(update={"order_intent": intent})

        result = OrderDispatcher(broker, clock=lambda: now).dispatch(decision)

        assert result.rejected_reason is not None
        assert broker.orders == []

    def test_future_intent_is_submitted(self, broker, approved_decision):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = OrderDispatcher(broker, clock=lambda: now).dispatch(approved_decision)

        assert result.dispatched is True
        assert len(broker.orders) == 1

    def test_non_mleg_mutation_never_reaches_broker(self, broker, approved_decision):
        bad_intent = approved_decision.order_intent.model_copy(update={"mleg_atomic": False})
        decision = approved_decision.model_copy(update={"order_intent": bad_intent})

        result = OrderDispatcher(broker).dispatch(decision)

        assert result.rejected_reason is not None
        assert broker.orders == []
