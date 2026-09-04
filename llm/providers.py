"""LLM provider implementations. Every call is stateless single-shot.

Contract: complete(system, user, schema, temperature) -> LLMResult
  - content: parsed dict (schema-constrained where the provider supports it)
  - usage:   {"input_tokens": int, "output_tokens": int, "calls": 1}
  - raises ProviderError on hard failure (caller decides fallback)

Token thrift by design:
  - no conversation history is kept or replayed
  - schema-constrained single response, no prose
  - max_tokens capped via config.LLM_MAX_TOKENS
"""
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests

import config

log = logging.getLogger(__name__)


class ProviderError(Exception):
    pass


def _parse_json_content(raw_content: str) -> dict:
    text = raw_content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :].strip()
            if text.endswith("```"):
                text = text[:-3].rstrip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ProviderError("featherless returned non-object JSON")
    return parsed


@dataclass
class LLMResult:
    content: dict
    model: str = ""
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "calls": 1})


class BaseProvider(ABC):
    name = "base"

    @abstractmethod
    def complete(self, system: str, user: str, schema: dict, temperature: float, model: str | None = None) -> LLMResult:
        ...

    @staticmethod
    def _empty_usage() -> dict:
        return {"input_tokens": 0, "output_tokens": 0, "calls": 1}


class OllamaProvider(BaseProvider):
    """Local Ollama. JSON Schema forced via `format` -> cannot emit non-JSON."""

    name = "ollama"

    def __init__(self) -> None:
        self.url = config.OLLAMA_URL
        self.model = config.OLLAMA_MODEL
        self.keep_alive = config.LLM_KEEP_ALIVE

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=3)
            models = [m.get("name", "") for m in r.json().get("models", [])]
            base = self.model.split(":")[0]
            return any(m.startswith(base) for m in models)
        except Exception:  # noqa: BLE001
            return False

    def complete(self, system: str, user: str, schema: dict, temperature: float, model: str | None = None) -> LLMResult:
        body = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            # Structured agent output must not spend its token budget in Qwen reasoning mode.
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": 8192,
                "num_predict": config.LLM_MAX_TOKENS,
            },
        }
        try:
            resp = requests.post(f"{self.url}/api/chat", json=body, timeout=config.LLM_TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout as exc:
            raise ProviderError(f"ollama timeout after {config.LLM_TIMEOUT_S}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"ollama call failed: {exc}") from exc

        content = json.loads(data["message"]["content"])
        usage = self._empty_usage()
        # Ollama reports nanoseconds; expose tokens so cost is always measurable
        usage["input_tokens"] = int(data.get("prompt_eval_count", 0) or 0)
        usage["output_tokens"] = int(data.get("eval_count", 0) or 0)
        return LLMResult(content=content, model=model or self.model, usage=usage)


class OpenAIProvider(BaseProvider):
    """OpenAI ChatCompletions with structured outputs (json_schema strict)."""

    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI  # lazy: only required when provider selected

        if not config.OPENAI_API_KEY:
            raise ProviderError("OPENAI_API_KEY missing in .env")
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = config.OPENAI_MODEL

    def complete(self, system: str, user: str, schema: dict, temperature: float, model: str | None = None) -> LLMResult:
        try:
            resp = self.client.chat.completions.create(
                model=model or self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "agent_report", "strict": True, "schema": schema},
                },
                temperature=temperature,
                max_tokens=config.LLM_MAX_TOKENS,
                timeout=config.LLM_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"openai call failed: {exc}") from exc
        content = json.loads(resp.choices[0].message.content or "{}")
        u = getattr(resp, "usage", None)
        usage = self._empty_usage()
        if u:
            usage["input_tokens"] = getattr(u, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(u, "completion_tokens", 0) or 0
        return LLMResult(content=content, model=model or self.model, usage=usage)


class FeatherlessProvider(BaseProvider):
    """Featherless OpenAI-compatible chat completions with JSON mode."""

    name = "featherless"

    def __init__(self) -> None:
        from openai import OpenAI  # lazy: only required when provider selected

        if not config.FEATHERLESS_API_KEY:
            raise ProviderError("FEATHERLESS_API_KEY missing in .env")
        if not config.FEATHERLESS_MODEL:
            raise ProviderError("FEATHERLESS_MODEL missing in .env")
        self.client = OpenAI(
            api_key=config.FEATHERLESS_API_KEY,
            base_url=config.FEATHERLESS_BASE_URL,
        )
        self.model = config.FEATHERLESS_MODEL

    def complete(self, system: str, user: str, schema: dict, temperature: float, model: str | None = None) -> LLMResult:
        effective_model = model or self.model
        system_with_schema = (
            f"{system}\nRequired output JSON schema:\n{json.dumps(schema, sort_keys=True)}"
        )
        request = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": config.LLM_MAX_TOKENS,
            "timeout": config.LLM_TIMEOUT_S,
        }
        if effective_model.lower().endswith("/glm-5.3-flash"):
            # Featherless otherwise returns the JSON in GLM's reasoning field.
            request["extra_body"] = {"chat_template_kwargs": {"clear_thinking": True}}
            request["reasoning_effort"] = "low"
        try:
            resp = self.client.chat.completions.create(**request)
            raw_content = getattr(resp.choices[0].message, "content", None)
            if not isinstance(raw_content, str) or not raw_content.strip():
                raise ProviderError("featherless returned empty content")
            content = _parse_json_content(raw_content)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"featherless call failed: {exc}") from exc
        usage = self._empty_usage()
        u = getattr(resp, "usage", None)
        if u:
            usage["input_tokens"] = getattr(u, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(u, "completion_tokens", 0) or 0
        return LLMResult(content=content, model=effective_model, usage=usage)


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API; schema enforced via a forced single tool call."""

    name = "anthropic"

    def __init__(self) -> None:
        import anthropic  # lazy

        if not config.ANTHROPIC_API_KEY:
            raise ProviderError("ANTHROPIC_API_KEY missing in .env")
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.ANTHROPIC_MODEL

    def complete(self, system: str, user: str, schema: dict, temperature: float, model: str | None = None) -> LLMResult:
        tool = {
            "name": "emit_report",
            "description": "Emit the structured agent report.",
            "input_schema": schema,
        }
        try:
            resp = self.client.messages.create(
                model=model or self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "emit_report"},
                max_tokens=config.LLM_MAX_TOKENS,
                timeout=config.LLM_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"anthropic call failed: {exc}") from exc
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise ProviderError("anthropic returned no tool_use block")
        content = dict(block.input)
        usage = self._empty_usage()
        if getattr(resp, "usage", None):
            usage["input_tokens"] = getattr(resp.usage, "input_tokens", 0) or 0
            usage["output_tokens"] = getattr(resp.usage, "output_tokens", 0) or 0
        return LLMResult(content=content, model=model or self.model, usage=usage)


_PROVIDERS = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "featherless": FeatherlessProvider,
    "anthropic": AnthropicProvider,
}
