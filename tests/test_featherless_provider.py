import json

import pytest

import config
from llm.client import ProviderError, _PROVIDERS, _build
from llm.providers import FeatherlessProvider


def test_featherless_provider_is_registered():
    assert "featherless" in _PROVIDERS


def test_featherless_provider_requires_key_and_model(monkeypatch):
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "featherless", raising=False)

    with pytest.raises(ProviderError):
        _build()


def test_featherless_provider_uses_openai_compatible_endpoint(monkeypatch):
    calls = {}

    class FakeCompletions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Message", (), {"content": json.dumps({"ok": True})})()},
                        )()
                    ],
                    "usage": type("Usage", (), {"prompt_tokens": 4, "completion_tokens": 3})(),
                },
            )()

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "test/model", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1", raising=False)

    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    result = FeatherlessProvider().complete("sys", "usr", schema, 0.2)

    assert result.content == {"ok": True}
    assert result.usage == {"input_tokens": 4, "output_tokens": 3, "calls": 1}
    assert calls["client"] == {
        "api_key": "test-key",
        "base_url": "https://api.featherless.ai/v1",
    }
    assert calls["model"] == "test/model"
    assert calls["response_format"] == {"type": "json_object"}
    assert json.dumps(schema, sort_keys=True) in calls["messages"][0]["content"]


def test_featherless_provider_accepts_per_call_model_override(monkeypatch):
    calls = {}

    class FakeCompletions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Message", (), {"content": "{}"})()},
                        )()
                    ],
                    "usage": None,
                },
            )()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "zai-org/GLM-5.3-Flash", raising=False)

    FeatherlessProvider().complete("sys", "usr", {"type": "object"}, 0.2, model="zai-org/GLM-5.2")

    assert calls["model"] == "zai-org/GLM-5.2"


def test_featherless_glm_flash_clears_thinking_for_structured_output(monkeypatch):
    calls = {}

    class FakeCompletions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Message", (), {"content": '{"ok": true}'})()},
                        )()
                    ],
                    "usage": None,
                },
            )()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "zai-org/GLM-5.3-Flash", raising=False)

    FeatherlessProvider().complete("sys", "usr", {"type": "object"}, 0.2)

    assert calls["extra_body"] == {"chat_template_kwargs": {"clear_thinking": True}}
    assert calls["reasoning_effort"] == "low"


def test_featherless_provider_rejects_empty_content(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message",
                                    (),
                                    {"content": "", "reasoning": '{"ok": true}'},
                                )(),
                            },
                        )()
                    ],
                    "usage": None,
                },
            )()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "test/model", raising=False)

    with pytest.raises(ProviderError, match="empty content"):
        FeatherlessProvider().complete("sys", "usr", {"type": "object"}, 0.2)


def test_featherless_provider_parses_json_code_fence(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message",
                                    (),
                                    {"content": '```json\n{"ok": true}\n```'},
                                )(),
                            },
                        )()
                    ],
                    "usage": None,
                },
            )()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "test/model", raising=False)

    result = FeatherlessProvider().complete("sys", "usr", {"type": "object"}, 0.2)

    assert result.content == {"ok": True}


def test_light_agent_uses_light_featherless_model(monkeypatch):
    from agents.base_agent import BaseAgent
    from llm.providers import LLMResult

    calls = {}

    class FakeProvider:
        def complete(self, **kwargs):
            calls.update(kwargs)
            return LLMResult(content={"agent": "TestAgent", "symbol": "SPY", "confidence": 0.5, "key_points": []})

    class TestAgent(BaseAgent):
        name = "TestAgent"
        model_tier = "light"
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

    monkeypatch.setattr(config, "LLM_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "featherless", raising=False)
    monkeypatch.setattr(config, "FEATHERLESS_LIGHT_MODEL", "zai-org/GLM-5.2", raising=False)
    monkeypatch.setattr("agents.base_agent.provider_available", lambda: True)
    monkeypatch.setattr("agents.base_agent.get_provider", lambda: FakeProvider())

    assert TestAgent().run({"symbol": "SPY"})["symbol"] == "SPY"
    assert calls["model"] == "zai-org/GLM-5.2"
