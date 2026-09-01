"""Offline alpaca-py compatibility tests for the concrete broker adapter."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alpaca.trading.enums import OrderClass, OrderSide, OrderType, PositionIntent, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

from execution.broker import AlpacaConcreteBroker


def _broker() -> AlpacaConcreteBroker:
    return AlpacaConcreteBroker(
        api_key="key",
        api_secret="secret",
        base_url="https://paper.example.test",
        account_id="account-1",
    )


def _request() -> LimitOrderRequest:
    return LimitOrderRequest(
        qty=1,
        type=OrderType.LIMIT,
        limit_price=1.5,
        time_in_force=TimeInForce.GTC,
        order_class=OrderClass.MLEG,
        legs=[
            OptionLegRequest(
                symbol="XSP241205P00550000",
                ratio_qty=1,
                side=OrderSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
            OptionLegRequest(
                symbol="XSP241205P00545000",
                ratio_qty=1,
                side=OrderSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
        ],
    )


def test_trading_client_uses_url_override():
    broker = _broker()
    with patch("alpaca.trading.client.TradingClient") as client_class:
        assert broker._client() is client_class.return_value

    client_class.assert_called_once_with(
        api_key="key",
        secret_key="secret",
        paper=True,
        url_override="https://paper.example.test",
    )


def test_submit_order_passes_typed_request_with_client_order_id():
    broker = _broker()
    client = MagicMock()
    client.submit_order.return_value = SimpleNamespace(id="order-1")
    broker._sdk_client = client

    order_id = broker.submit_order(_request(), "intent-1")

    assert order_id == "order-1"
    submitted = client.submit_order.call_args.kwargs["order_data"]
    assert isinstance(submitted, LimitOrderRequest)
    assert submitted.client_order_id == "intent-1"
    assert submitted.order_class == OrderClass.MLEG


def test_find_order_by_client_order_id_uses_sdk_lookup():
    broker = _broker()
    client = MagicMock()
    client.get_order_by_client_id.return_value = SimpleNamespace(id="order-1")
    broker._sdk_client = client

    assert broker.find_order_by_client_order_id("intent-1") == "order-1"
    client.get_order_by_client_id.assert_called_once_with(client_id="intent-1")


class _StatusError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_position_qty_is_signed_contract_quantity():
    broker = _broker()
    client = MagicMock()
    client.get_all_positions.return_value = [
        SimpleNamespace(
            symbol="XSP-long", qty="3", side="long", market_value="999999", unrealized_pl="1"
        ),
        SimpleNamespace(
            symbol="XSP-short", qty="3", side="short", market_value="999999", unrealized_pl="-1"
        ),
    ]
    broker._sdk_client = client

    positions = broker.get_positions()

    assert [(position.symbol, position.quantity) for position in positions] == [
        ("XSP-long", 3), ("XSP-short", -3)
    ]


def test_position_snapshot_failure_is_distinct_from_empty_positions():
    from execution.broker import BrokerSnapshotUnavailable

    broker = _broker()
    client = MagicMock()
    client.get_all_positions.side_effect = TimeoutError("offline")
    broker._sdk_client = client

    with pytest.raises(BrokerSnapshotUnavailable):
        broker.get_positions()


def test_client_order_lookup_returns_none_only_for_explicit_not_found():
    broker = _broker()
    client = MagicMock()
    client.get_order_by_client_id.side_effect = _StatusError(404)
    broker._sdk_client = client

    assert broker.find_order_by_client_order_id("missing") is None


def test_client_order_lookup_failure_does_not_become_not_found():
    from execution.broker import BrokerLookupError

    broker = _broker()
    client = MagicMock()
    client.get_order_by_client_id.side_effect = TimeoutError("offline")
    broker._sdk_client = client

    with pytest.raises(BrokerLookupError):
        broker.find_order_by_client_order_id("unknown")



def test_position_snapshot_parses_occ_option_metadata():
    broker = _broker()
    client = MagicMock()
    client.get_all_positions.return_value = [
        SimpleNamespace(
            symbol="XSP241205P00550000", qty="3", side="short",
            market_value="-300", unrealized_pl="0",
        )
    ]
    broker._sdk_client = client

    position = broker.get_positions()[0]

    assert position.quantity == -3
    assert position.position_type == "put"
    assert position.strike == 550.0
    assert position.expiration.isoformat() == "2024-12-05T00:00:00+00:00"
