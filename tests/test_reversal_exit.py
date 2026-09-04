from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from execution.exit_policy import exit_decision
from execution import position_manager


def _position(**extra):
    value = {
        "id": "position-1",
        "status": "OPEN",
        "underlying": "AAPL",
        "qty": 1,
        "strategy_type": "LONG_CALL",
        "net_credit_or_debit_per_unit": -4.82,
        "entry_price": 4.82,
        "take_profit_price": 6.51,
        "stop_loss_price": 3.62,
        "time_stop_minutes": 1,
        "legs": [{"action": "BUY", "symbol": "AAPL260919C00300000", "expiry": "2026-09-19"}],
    }
    value.update(extra)
    return value


def _quote(bid):
    return {"AAPL260919C00300000": SimpleNamespace(bid=bid, ask=bid + 0.1, mid=bid + 0.05)}


def test_tp_and_sl_use_levels_based_on_actual_fill():
    result = exit_decision(_position(), _quote(6.51), today=date(2026, 8, 28))

    assert result["reason"] == "take_profit"

    result = exit_decision(_position(), _quote(3.62), today=date(2026, 8, 28))

    assert result["reason"] == "stop_loss"


def test_critical_news_closes_before_indicator_reversal():
    result = exit_decision(
        _position(), _quote(4.82), today=date(2026, 8, 28), news_risk={
            "event_risk": "CRITICAL",
            "confidence": 0.95,
            "headlines": ["Issuer announces a trading halt"],
            "source": "alpaca_py_sdk",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert result["reason"] == "critical_news"


def test_unverified_critical_news_does_not_close():
    result = exit_decision(
        _position(), _quote(4.82), today=date(2026, 8, 28), news_risk="CRITICAL"
    )

    assert result is None


def test_hard_price_exit_precedes_critical_news():
    result = exit_decision(
        _position(), _quote(6.51), today=date(2026, 8, 28), news_risk={
            "event_risk": "CRITICAL",
            "confidence": 0.95,
            "headlines": ["Issuer announces a trading halt"],
            "source": "alpaca_py_sdk",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert result["reason"] == "take_profit"


def test_reversal_requires_two_completed_bars_and_two_votes_each():
    history = [
        {"ema_fast": 9.0, "ema_slow": 10.0, "price": 9.5, "vwap": 10.0, "momentum": -1.0},
        {"ema_fast": 8.0, "ema_slow": 10.0, "price": 9.0, "vwap": 10.0, "momentum": -2.0},
    ]
    result = exit_decision(
        _position(), _quote(4.82), today=date(2026, 8, 28), indicator_history=history
    )

    assert result["reason"] == "confirmed_reversal"
    assert result["reversal"]["bars_confirmed"] == 2


def test_single_noisy_reversal_bar_and_time_stop_do_not_close():
    history = [{
        "ema_fast": 9.0, "ema_slow": 10.0, "price": 9.5,
        "vwap": 10.0, "momentum": -1.0,
    }]

    result = exit_decision(
        _position(), _quote(4.82), today=date(2026, 8, 28), indicator_history=history
    )

    assert result is None


def test_stale_or_wide_quote_cannot_trigger_indicator_reversal(monkeypatch):
    monkeypatch.setattr(position_manager.config, "EXIT_QUOTE_MAX_AGE_SECONDS", 30, raising=False)
    stale = SimpleNamespace(
        bid=4.82,
        ask=5.82,
        mid=5.32,
        spread_pct=0.18,
        quote_timestamp=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    )
    history = [
        {"ema_fast": 9.0, "ema_slow": 10.0, "price": 9.5, "vwap": 10.0, "momentum": -1.0},
        {"ema_fast": 8.0, "ema_slow": 10.0, "price": 9.0, "vwap": 10.0, "momentum": -2.0},
    ]

    result = exit_decision(
        _position(), {"AAPL260919C00300000": stale},
        today=date(2026, 8, 28), indicator_history=history,
    )

    assert result is None


def test_position_manager_passes_indicator_history_and_news_to_exit_policy(monkeypatch):
    position = _position(
        indicator_history=[{"ema_fast": 9, "ema_slow": 10}],
        news_risk={"event_risk": "HIGH"},
    )
    data = {"positions": [position], "daily": {}}
    seen = {}
    monkeypatch.setattr(position_manager, "reconcile_order_intents", lambda: [])
    monkeypatch.setattr(position_manager, "reconcile_closing_positions", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_pending_entries", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_with_broker", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_untracked_filled_orders", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_unfilled", lambda _data: [])
    monkeypatch.setattr(position_manager, "_chain_map", lambda _underlyings: {})

    def fake_exit(_pos, _chain, _today, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(position_manager, "exit_decision", fake_exit)

    position_manager.manage_positions(data)

    assert seen == {
        "indicator_history": position["indicator_history"],
        "news_risk": position["news_risk"],
    }


def test_position_manager_refreshes_indicator_history_for_monitored_positions(monkeypatch):
    position = _position(monitor_context_enabled=True, indicator_history=[])
    data = {"positions": [position], "daily": {}}
    monkeypatch.setattr(position_manager, "get_recent_news", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(position_manager, "reconcile_order_intents", lambda: [])
    monkeypatch.setattr(position_manager, "reconcile_closing_positions", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_pending_entries", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_with_broker", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_untracked_filled_orders", lambda _data: [])
    monkeypatch.setattr(position_manager, "reconcile_unfilled", lambda _data: [])
    monkeypatch.setattr(position_manager, "_chain_map", lambda _underlyings: {
        "AAPL260919C00300000": SimpleNamespace(bid=4.82, ask=4.92, mid=4.87),
    })
    monkeypatch.setattr(
        position_manager,
        "get_intraday_bars",
        lambda *_args, **_kwargs: pd.DataFrame({"close": range(100, 140), "volume": [1000] * 40}),
    )
    monkeypatch.setattr(position_manager, "exit_decision", lambda *_args, **_kwargs: None)

    position_manager.manage_positions(data)

    assert len(position["indicator_history"]) == 1
    assert position["indicator_history"][0]["ema_fast"] > position["indicator_history"][0]["ema_slow"]


def test_position_manager_refreshes_grounded_news_risk(monkeypatch):
    position = _position(monitor_context_enabled=True)
    monkeypatch.setattr(position_manager, "get_recent_news", lambda *_args, **_kwargs: [
        {"headline": "Issuer announces a trading halt"},
    ])

    class FakeNewsAgent:
        def run(self, payload):
            assert payload["headlines"] == ["Issuer announces a trading halt"]
            return {"event_risk": "CRITICAL", "confidence": 0.95}

    monkeypatch.setattr(position_manager, "NewsEarningsAgent", FakeNewsAgent)

    position_manager._refresh_news_risk(position)

    assert position["news_risk"]["event_risk"] == "CRITICAL"
    assert position["news_risk"]["headlines"] == ["Issuer announces a trading halt"]
    assert position["news_risk"]["source"] == "alpaca_py_sdk"
    assert position["news_risk"]["checked_at"]


def test_position_manager_does_not_refresh_news_on_every_monitor_cycle(monkeypatch):
    position = _position(
        monitor_context_enabled=True,
        news_checked_at=datetime.now(timezone.utc).isoformat(),
    )
    calls = []
    monkeypatch.setattr(position_manager, "get_recent_news", lambda *_args, **_kwargs: calls.append(1))

    position_manager._refresh_news_risk(position)

    assert calls == []
