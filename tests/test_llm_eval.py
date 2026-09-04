"""Tests for the provider-agnostic LLM layer + evaluation store (no network)."""
import json

import pytest

import config
from llm.client import ProviderError, _PROVIDERS, _build, provider_available
from llm.providers import LLMResult, OllamaProvider


# ------------------------------------------------------------------ providers
def test_provider_registry_complete():
    assert set(_PROVIDERS) == {"ollama", "openai", "featherless", "anthropic"}


def test_chief_schema_allows_only_single_leg_long_option_entries():
    from agents.strategy_decision_agent import StrategyDecisionAgent

    schema = StrategyDecisionAgent.schema
    assert schema["properties"]["strategy_type"]["enum"] == ["WAIT", "LONG_CALL", "LONG_PUT"]
    assert schema["properties"]["legs"]["items"]["properties"]["action"]["enum"] == ["BUY"]


def test_build_default_is_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama", raising=False)
    p = _build()
    assert isinstance(p, OllamaProvider)
    assert p.name == "ollama"


def test_build_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "skynet", raising=False)
    with pytest.raises(ProviderError):
        _build()


def test_ollama_result_usage_shape():
    r = LLMResult(content={"a": 1})
    assert r.usage == {"input_tokens": 0, "output_tokens": 0, "calls": 1}


def test_disabled_llm_never_calls_provider(monkeypatch):
    from agents.base_agent import BaseAgent

    class TestAgent(BaseAgent):
        name = "TestAgent"
        schema = {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "symbol": {"type": "string"},
                "confidence": {"type": "number"},
                "key_points": {"type": "array"},
            },
            "required": ["agent", "symbol", "confidence", "key_points"],
        }
        fallback = staticmethod(lambda: {"symbol": "SPY", "confidence": 0.0, "key_points": []})

    calls = {"count": 0}

    class FakeProvider:
        def complete(self, *_args, **_kwargs):
            calls["count"] += 1
            return LLMResult(
                content={"agent": "TestAgent", "symbol": "SPY", "confidence": 1.0, "key_points": []}
            )

    monkeypatch.setattr(config, "LLM_ENABLED", False, raising=False)
    monkeypatch.setattr("agents.base_agent.get_provider", lambda: FakeProvider())

    result = TestAgent().run({"symbol": "SPY"})

    assert calls["count"] == 0
    assert result["degraded"] is True


def test_ollama_complete_parses_usage(monkeypatch):
    """Fake the HTTP layer; verify JSON parse + token accounting."""
    class FakeResp:
        def raise_for_status(self): ...

        def json(self):
            return {
                "message": {"content": json.dumps({"bias": "BULLISH"})},
                "prompt_eval_count": 120,
                "eval_count": 45,
            }

    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["body"] = json
        return FakeResp()

    monkeypatch.setattr("llm.providers.requests.post", fake_post)
    monkeypatch.setattr(config, "LLM_MAX_TOKENS", 777, raising=False)
    p = OllamaProvider()
    out = p.complete("sys", "usr", {"type": "object"}, 0.2)
    assert out.content == {"bias": "BULLISH"}
    assert out.usage["input_tokens"] == 120
    assert out.usage["output_tokens"] == 45
    assert calls["body"]["options"]["num_predict"] == 777
    assert calls["body"]["keep_alive"] == config.LLM_KEEP_ALIVE
    assert calls["body"]["think"] is False


def test_agent_rejects_invalid_enum_and_out_of_range_confidence():
    from agents.underlying_trend_agent import UnderlyingTrendAgent

    report = {
        "agent": "wrong",
        "symbol": "SPY",
        "confidence": 1.4,
        "key_points": [],
        "bias": "MAYBE",
        "trend_strength": "STRONG",
    }

    assert UnderlyingTrendAgent()._validate(report) is False


def test_openai_provider_requires_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "", raising=False)
    with pytest.raises(ProviderError):
        _build()


def test_provider_availability_fails_closed_when_remote_credentials_are_missing(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "featherless", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "", raising=False)

    assert provider_available() is False


# ------------------------------------------------------------- evaluation db
def test_store_sync_and_stats(tmp_path, monkeypatch):
    from evaluation import store

    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    ledger_data = {
        "positions": [
            {
                "id": "t1", "status": "CLOSED", "underlying": "SPY",
                "strategy_type": "BULL_PUT_SPREAD", "qty": 1,
                "net_credit_or_debit_per_unit": 0.29, "realized_pl": 12.0,
                "opened_at": "2026-08-25T14:00:00+00:00",
                "closed_at": "2026-08-25T15:00:00+00:00",
                "exit_reason": "take_profit", "max_loss_usd": 71.0,
            },
            {
                "id": "t2", "status": "CLOSED", "underlying": "AAPL",
                "strategy_type": "BULL_PUT_SPREAD", "qty": 1,
                "net_credit_or_debit_per_unit": 0.5, "realized_pl": -30.0,
                "opened_at": "2026-08-25T14:00:00+00:00",
                "closed_at": "2026-08-25T16:00:00+00:00",
                "exit_reason": "stop_loss", "max_loss_usd": 50.0,
            },
            {"id": "t3", "status": "OPEN", "underlying": "SPY",
             "strategy_type": "IRON_CONDOR", "qty": 1},
        ]
    }
    new = store.sync_from_ledger(ledger_data)
    assert len(new) == 2  # OPEN position ignored
    assert store.sync_from_ledger(ledger_data) == []  # idempotent upsert

    s = store.stats()
    assert s["total_closed"] == 2
    assert s["win_rate"] == 0.5
    assert s["by_strategy"]["BULL_PUT_SPREAD"]["n"] == 2
    assert s["by_strategy"]["BULL_PUT_SPREAD"]["total_pl"] == -18.0

    assert len(store.recent_trades(5)) == 2


def test_evaluation_persists_postmortem_and_distills_without_embeddings(tmp_path, monkeypatch):
    from evaluation import evaluator, memory

    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "EMBED_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(config, "EVALUATION_ENABLED", True, raising=False)
    monkeypatch.setattr(memory, "_db", None)
    monkeypatch.setattr(memory, "_lance_ok", False)
    monkeypatch.setattr(memory, "_embed_ok", None)
    monkeypatch.setattr(evaluator, "update_lessons", lambda: {"updated": True, "n_lessons": 1})

    data = {
        "positions": [{
            "id": "momentum-1", "status": "CLOSED", "underlying": "AAPL",
            "strategy_type": "LONG_CALL", "qty": 1,
            "net_credit_or_debit_per_unit": -4.15, "realized_pl": 10.0,
            "opened_at": "2026-08-31T14:00:00+00:00",
            "closed_at": "2026-08-31T15:00:00+00:00",
            "exit_reason": "take_profit", "max_loss_usd": 415.0,
        }],
    }

    result = evaluator.run_after_cycle(data, [{"symbol": "AAPL", "reports": {}}])

    assert result["new_closed_trades"] == 1
    assert result["postmortems_written"] == 1
    assert result["updated"] is True
    assert memory.count() == 1
    assert memory.recent(1)[0]["realized_pl"] == 10.0


def test_evaluation_records_every_cycle_action(tmp_path, monkeypatch):
    from evaluation import evaluator, store

    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "EVALUATION_ENABLED", True, raising=False)

    result = evaluator.run_after_cycle(
        {"positions": []},
        [
            {"symbol": "SPY", "action": "WAIT"},
            {"symbol": "AAPL", "action": "REJECTED"},
        ],
    )

    assert result["actions_reviewed"] == 2
    assert result["action_counts"] == {"WAIT": 1, "REJECTED": 1}
    assert store.latest_evaluation("cycle_actions")["action_counts"] == result["action_counts"]


def test_load_lessons_missing_file(tmp_path, monkeypatch):
    import orchestrator.pipeline as pl

    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    assert pl._load_lessons() == []
