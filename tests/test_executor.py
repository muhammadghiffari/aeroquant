"""Order construction tests for option entries and exits."""
from types import SimpleNamespace

import pytest

from execution import executor


def test_submit_long_call_uses_single_leg_limit_order(monkeypatch):
    captured = {}

    class FakeClient:
        def submit_order(self, order_data):
            raise AssertionError("safe wrapper should intercept submission")

    def fake_safe(_name, _call, **kwargs):
        captured["request"] = kwargs["order_data"]
        return SimpleNamespace(id="order-1", client_order_id="agent-aapl-test", status="accepted")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(executor.alpaca_client, "safe", fake_safe)

    result = executor.submit_strategy(
        {"symbol": "AAPL", "strategy_type": "LONG_CALL", "legs": []},
        {
            "adjusted_qty": 1,
            "recomputed": {
                "resolved_legs": [
                    {"symbol": "AAPL260902C00305000", "action": "BUY", "mid": 10.0}
                ]
            },
        },
    )

    request = captured["request"]
    assert request.symbol == "AAPL260902C00305000"
    assert request.side.value == "buy"
    assert request.order_class is None
    assert request.legs is None
    assert request.limit_price == 11.5
    assert result["status"] == "accepted"


def test_submit_strategy_uses_caller_persisted_client_order_id(monkeypatch):
    captured = {}

    class FakeClient:
        def submit_order(self, order_data):
            raise AssertionError("safe wrapper should intercept submission")

    def fake_safe(_name, _call, **kwargs):
        captured["request"] = kwargs["order_data"]
        return SimpleNamespace(id="order-1", client_order_id="intent-123", status="accepted")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(executor.alpaca_client, "safe", fake_safe)

    executor.submit_strategy(
        {"symbol": "AAPL", "strategy_type": "LONG_CALL", "legs": []},
        {"adjusted_qty": 1, "recomputed": {"resolved_legs": [
            {"symbol": "AAPL260902C00305000", "action": "BUY", "mid": 10.0}
        ]}},
        client_order_id="intent-123",
    )

    assert captured["request"].client_order_id == "intent-123"


def test_submit_long_option_does_not_price_above_executable_ask(monkeypatch):
    captured = {}

    class FakeClient:
        def submit_order(self, order_data):
            raise AssertionError("safe wrapper should intercept submission")

    def fake_safe(_name, _call, **kwargs):
        captured["request"] = kwargs["order_data"]
        return SimpleNamespace(id="order-1", client_order_id="agent-aapl-test", status="accepted")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(executor.alpaca_client, "safe", fake_safe)

    executor.submit_strategy(
        {"symbol": "AAPL", "strategy_type": "LONG_CALL", "legs": []},
        {"adjusted_qty": 1, "recomputed": {"resolved_legs": [
            {"symbol": "AAPL260902C00305000", "action": "BUY", "mid": 10.0, "bid": 9.8, "ask": 10.2}
        ]}},
    )

    assert captured["request"].limit_price == 10.2


def test_close_single_leg_uses_simple_limit_order(monkeypatch):
    captured = {}

    class FakeClient:
        def submit_order(self, order_data):
            raise AssertionError("safe wrapper should intercept submission")

    def fake_safe(_name, _call, **kwargs):
        captured["request"] = kwargs["order_data"]
        return SimpleNamespace(id="close-1", client_order_id="close-position-1", status="accepted")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(executor.alpaca_client, "safe", fake_safe)

    result = executor.close_position(
        {
            "id": "position-1",
            "qty": 1,
            "strategy_type": "LONG_CALL",
            "net_credit_or_debit_per_unit": -10.0,
            "legs": [{"symbol": "AAPL260902C00305000", "action": "BUY"}],
        },
        {"AAPL260902C00305000": SimpleNamespace(mid=8.0)},
    )

    request = captured["request"]
    assert request.symbol == "AAPL260902C00305000"
    assert request.side.value == "sell"
    assert request.position_intent.value == "sell_to_close"
    assert request.order_class is None
    assert request.legs is None
    assert request.limit_price == 6.8
    assert result["status"] == "accepted"


def test_close_long_option_uses_executable_bid_as_sell_floor(monkeypatch):
    captured = {}

    class FakeClient:
        def submit_order(self, order_data):
            raise AssertionError("safe wrapper should intercept submission")

    def fake_safe(_name, _call, **kwargs):
        captured["request"] = kwargs["order_data"]
        return SimpleNamespace(id="close-1", client_order_id="close-position-1", status="accepted")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(executor.alpaca_client, "safe", fake_safe)

    result = executor.close_position(
        {
            "id": "position-1",
            "qty": 1,
            "strategy_type": "LONG_CALL",
            "net_credit_or_debit_per_unit": -4.82,
            "legs": [{"symbol": "SPY260903C00769000", "action": "BUY"}],
        },
        {"SPY260903C00769000": SimpleNamespace(bid=4.83, mid=5.10)},
    )

    assert captured["request"].limit_price == 4.83
    assert result["estimated_realized_pl"] == 1.0


def test_close_orphaned_long_leg_uses_simple_limit_order(monkeypatch):
    captured = {}

    class FakeClient:
        def submit_order(self, order_data):
            raise AssertionError("safe wrapper should intercept submission")

    def fake_safe(_name, _call, **kwargs):
        captured["request"] = kwargs["order_data"]
        return SimpleNamespace(id="heal-1", status="accepted")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(executor.alpaca_client, "safe", fake_safe)
    from data_engine import option_data

    monkeypatch.setattr(
        option_data,
        "fetch_chain",
        lambda *_args, **_kwargs: [SimpleNamespace(symbol="AAPL260902C00305000", mid=8.0)],
    )

    result = executor.close_single_leg("AAPL260902C00305000", "long", 1)

    request = captured["request"]
    assert request.symbol == "AAPL260902C00305000"
    assert request.side.value == "sell"
    assert request.position_intent.value == "sell_to_close"
    assert request.order_class is None
    assert request.legs is None
    assert request.limit_price == 5.6
    assert result["status"] == "accepted"


def test_close_position_returns_none_when_submission_fails(monkeypatch):
    class FakeClient:
        def get_order_by_client_id(self, _client_order_id):
            raise RuntimeError("not found")

    monkeypatch.setattr(executor.alpaca_client, "trading_client", lambda: FakeClient())
    monkeypatch.setattr(
        executor.alpaca_client,
        "safe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rejected")),
    )

    result = executor.close_position(
        {
            "id": "position-1",
            "qty": 1,
            "strategy_type": "LONG_CALL",
            "net_credit_or_debit_per_unit": -10.0,
            "legs": [{"symbol": "AAPL260902C00305000", "action": "BUY"}],
        },
        {"AAPL260902C00305000": SimpleNamespace(mid=8.0)},
    )

    assert result is None


def test_submit_strategy_rejects_non_single_leg_even_if_called_directly():
    with pytest.raises(ValueError, match="single long option"):
        executor.submit_strategy(
            {"symbol": "AAPL", "strategy_type": "BULL_PUT_SPREAD", "legs": []},
            {"adjusted_qty": 1, "recomputed": {"resolved_legs": [
                {"symbol": "AAPL260902P00305000", "action": "SELL", "mid": 2.0},
                {"symbol": "AAPL260902P00300000", "action": "BUY", "mid": 1.0},
            ]}},
        )
