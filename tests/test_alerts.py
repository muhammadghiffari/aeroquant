import config


def test_alert_event_id_is_idempotent(monkeypatch, tmp_path):
    import alerts

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "chat", raising=False)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(alerts.requests, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or FakeResponse())

    first = alerts.send_alert("order_submitted", "SPY order-1", event_id="entry:order-1")
    second = alerts.send_alert("order_submitted", "SPY order-1", event_id="entry:order-1")

    assert first["sent"] is True
    assert second == {"sent": False, "reason": "duplicate", "event_id": "entry:order-1"}
    assert len(calls) == 1


def test_trade_events_cover_entry_and_close_lifecycle(monkeypatch):
    import alerts

    sent = []
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda event, message, event_id=None: sent.append((event, message, event_id))
        or {"sent": True, "event_id": event_id},
    )

    alerts.notify_cycle_events(
        [{
            "symbol": "AAPL", "action": "EXECUTED",
            "execution": {
                "order_id": "entry-1", "client_order_id": "agent-aapl-1",
                "status": "filled", "qty": 1,
            },
        }],
        [
            {
                "position_id": "position-1", "reason": "take_profit",
                "order_id": "close-1", "status": "accepted", "estimated_realized_pl": 9.5,
            },
            {
                "position_id": "position-1", "reason": "close_filled",
                "realized_pl": 10.0, "order_id": "close-1",
            },
        ],
        {"positions": [{"id": "position-1", "strategy_type": "LONG_CALL"}]},
    )

    assert [event[0] for event in sent] == [
        "order_submitted", "order_filled", "close_requested", "close_filled",
    ]
    assert sent[-1][2] == "close-filled:position-1:close-1"


def test_daily_summary_reports_exchange_day(monkeypatch):
    import alerts

    sent = {}
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda event, message, event_id=None: sent.update(
            event=event, message=message, event_id=event_id
        ) or {"sent": True},
    )

    result = alerts.send_daily_summary(
        {
            "daily": {"2026-09-01": {"realized_pl": 12.5}},
            "positions": [{"status": "CLOSED", "closed_at": "2026-09-01T15:00:00+00:00"}],
        },
        summary_date=__import__("datetime").date(2026, 9, 1),
    )

    assert result["sent"] is True
    assert sent == {
        "event": "daily_market_close",
        "message": "date=2026-09-01 realized_pl=$12.50 closed=1 open=0",
        "event_id": "daily-close:2026-09-01",
    }


def test_pending_close_retries_the_same_idempotent_request(monkeypatch):
    import alerts

    sent = []
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda event, message, event_id=None: sent.append((event, message, event_id))
        or {"sent": True},
    )

    alerts.notify_cycle_events(
        [],
        [{"id": "position-1", "reason": "close_pending", "broker_status": "accepted"}],
        {"positions": [{
            "id": "position-1", "underlying": "AAPL", "strategy_type": "LONG_CALL",
            "closing_order_id": "close-1",
        }]},
    )

    assert sent[0][0] == "close_requested"
    assert sent[0][2] == "close-requested:close-1"


def test_cycle_flow_alert_exposes_every_pipeline_stage(monkeypatch):
    import alerts

    sent = []
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda event, message, event_id=None: sent.append((event, message, event_id))
        or {"sent": True, "event_id": event_id},
    )
    cycle = {
        "cycle_id": "cycle-1",
        "timestamp": "2026-09-03T14:00:00+00:00",
        "dry_run": False,
        "account": {"equity": 100000, "buying_power": 50000},
        "mcp_context": {"clock": {"is_open": True}},
        "llm_available": True,
        "llm_usage": {"calls": 5},
    }
    result = {
        "symbol": "AAPL",
        "action": "REJECTED",
        "quant_gate": {
            "entry_mode": "UNDERLYING_HISTORY_PROXY",
            "direction": "BULLISH",
            "entry_confidence": {
                "state": "GREEN_PROXY", "source": "stock_bars",
                "probability": 0.55, "lower_bound": 0.42,
            },
            "contract_confidence": {"state": "WAIT_DATA", "sample_size": 0},
            "candidates": [{
                "symbol": "AAPL260918C00325000", "bid": 8.0, "ask": 8.1,
                "live_quote_activity": {"dominant_side": "BID_HEAVY"},
            }],
        },
        "shadow": {"recorded": 1, "resolved": 0},
        "reports": {
            "trend": {"agent": "UnderlyingTrendAgent", "_usage": {"calls": 1}},
            "volatility": {"agent": "VolatilityAgent", "_usage": {"calls": 1}},
            "news": {"agent": "NewsEarningsAgent", "_usage": {"calls": 1}},
            "technical": {"agent": "TechnicalManager", "_usage": {"calls": 1}},
            "context": {"agent": "ContextManager", "_usage": {"calls": 1}},
            "proposal": {
                "agent": "StrategyDecisionAgent", "candidate_id": "NONE",
                "strategy_type": "WAIT", "rationale": "test rationale",
            },
            "risk_decision": {
                "agent": "RiskManagerAgent", "decision": "REJECTED",
                "checks": {"max_loss_within_limit": False},
                "notes": "test risk rejection",
            },
        },
        "rejection_reason": "risk rejection",
    }

    alerts.notify_cycle_events([result], [], {"positions": []}, cycle=cycle)

    assert len(sent) == 1
    event, message, event_id = sent[0]
    assert event == "cycle_flow"
    assert event_id == "cycle-flow:cycle-1:AAPL:0"
    assert all(stage in message for stage in (
        "MARKET/DATA", "QUANT", "SHADOW", "LIVE QUOTE", "LLM",
        "STRATEGY", "RISK", "EXECUTION",
    ))
    assert "GREEN_PROXY" in message
    assert "WAIT_DATA" in message
    assert "BID_HEAVY" in message
    assert "risk rejection" in message


def test_cycle_flow_alert_chunks_oversized_messages(monkeypatch):
    import alerts

    sent = []
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda event, message, event_id=None: sent.append((event, message, event_id))
        or {"sent": True, "event_id": event_id},
    )
    cycle = {"cycle_id": "cycle-long", "timestamp": "now", "dry_run": False}
    result = {
        "symbol": "SPY", "action": "WAIT_SEE",
        "quant_gate": {}, "reports": {},
        "rejection_reason": "x" * 10000,
    }

    alerts.notify_cycle_events([result], [], {"positions": []}, cycle=cycle)

    assert len(sent) > 1
    assert all(len(message) <= alerts.TELEGRAM_MESSAGE_LIMIT for _, message, _ in sent)
    assert [event_id for _, _, event_id in sent] == [
        f"cycle-flow:cycle-long:SPY:{index}" for index in range(len(sent))
    ]


def test_stage_events_are_ordered_and_secret_free(monkeypatch, tmp_path):
    import alerts

    sent = []
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "chat", raising=False)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda event, message, event_id=None: sent.append((event, message, event_id))
        or {"sent": True, "event_id": event_id},
    )

    stages = [
        "CYCLE_STARTED", "QUANT_COMPLETED", "STRATEGY_DECIDED",
        "RISK_DECIDED", "ORDER_SUBMITTED", "CYCLE_COMPLETED",
    ]
    for sequence, stage in enumerate(stages):
        alerts.emit_stage(
            "cycle-1", "SPY", stage, sequence,
            {"status": "complete", "summary": "safe", "prompt": "API_KEY=SECRET_KEY"},
        )

    assert [event_id for _, _, event_id in sent] == [
        f"cycle-1:SPY:{stage}:{sequence}" for sequence, stage in enumerate(stages)
    ]
    assert all(secret not in message for _, message, _ in sent for secret in (
        "API_KEY", "SECRET_KEY", "BOT_TOKEN", "API_KEY=SECRET_KEY",
    ))
    assert not (tmp_path / "telegram_outbox.jsonl").exists()


def test_failed_stage_event_stays_in_outbox_until_next_successful_flush(monkeypatch, tmp_path):
    import json
    import alerts
    import requests

    attempts = {"count": 0}

    class FakeResponse:
        def raise_for_status(self):
            return None

    def post(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise requests.RequestException("temporary outage")
        return FakeResponse()

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "chat", raising=False)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(alerts.requests, "post", post)

    first = alerts.emit_stage("cycle-1", "SPY", "CYCLE_STARTED", 0, {"status": "started"})

    assert first["sent"] is False
    assert first["reason"] == "request_failed"
    outbox = tmp_path / "telegram_outbox.jsonl"
    assert outbox.exists()
    assert json.loads(outbox.read_text(encoding="utf-8").splitlines()[0])["attempts"] == 1

    result = alerts.flush_telegram_outbox()

    assert result[0]["sent"] is True
    assert not outbox.exists()


def test_telegram_health_check_validates_bot_and_chat(monkeypatch):
    import alerts

    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"ok": True, "result": {}})

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "chat", raising=False)
    monkeypatch.setattr(alerts.requests, "get", get)

    assert alerts.telegram_health_check() == (True, "ok")
    assert [url.rsplit("/", 1)[-1] for url, _ in calls] == ["getMe", "getChat"]
    assert all(kwargs["timeout"] == 5 for _, kwargs in calls)
