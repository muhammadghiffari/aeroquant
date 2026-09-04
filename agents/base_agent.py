"""Base LLM agent over the provider-agnostic llm/ layer.

Design decisions (see docs/PRD.md section 6 + discussion):
- ONE model per provider for every agent (VRAM/cost-constrained); agents
  differ by system prompt only. Calls are STATELESS single-shot -- no
  session, no replayed history (token thrift).
- Schema-constrained output per provider (Ollama `format`, OpenAI
  json_schema strict, Anthropic forced tool call) -> the model cannot emit
  free-form prose; we still validate required fields and retry once.
- Any hard failure (timeout / connection / invalid twice) returns the
  agent-specific neutral fallback flagged `degraded: true` (spec section 16).
- Token usage of every call is attached to the agent report (`_usage`)
  so waste is always measurable.
"""
import json
import logging
import math
from typing import Callable

from llm import ProviderError, get_provider, provider_available

import config

log = logging.getLogger(__name__)


class BaseAgent:
    """Subclasses provide name, system_prompt, schema, fallback."""

    name: str = "BaseAgent"
    system_prompt: str = ""
    schema: dict = {}
    fallback: Callable[[], dict] = lambda: {}
    model_tier: str = "heavy"

    def __init__(self):
        self.temperature = 0.2
        self.last_usage: dict = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def _model_override(self) -> str | None:
        if config.LLM_PROVIDER != "featherless":
            return None
        if self.model_tier == "light":
            return config.FEATHERLESS_LIGHT_MODEL or config.FEATHERLESS_MODEL
        return config.FEATHERLESS_HEAVY_MODEL or config.FEATHERLESS_MODEL

    # ------------------------------------------------------------------ api
    def run(self, payload: dict) -> dict:
        """payload -> validated report dict (never raises)."""
        out = self._call_llm(payload)
        if out is not None and self._validate(out):
            out["_usage"] = dict(self.last_usage)
            return out
        if out is not None:
            log.warning("%s output failed validation after retry -- fallback", self.name)
        fb = self.fallback()
        fb["degraded"] = True
        fb["agent"] = self.name
        fb["_usage"] = dict(self.last_usage)
        return fb

    # ------------------------------------------------------------- internals
    def _call_llm(self, payload: dict) -> dict | None:
        if not config.LLM_ENABLED:
            return None
        try:
            available = provider_available()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: provider availability check failed: %s", self.name, exc)
            return None
        if not available:
            return None
        messages_user = json.dumps(payload, default=str)
        attempts = config.LLM_JSON_RETRY + 1
        for attempt in range(1, attempts + 1):
            try:
                result = get_provider().complete(
                    system=self.system_prompt,
                    user=messages_user,
                    schema=self.schema,
                    temperature=self.temperature,
                    model=self._model_override(),
                )
                self.last_usage = {
                    "input_tokens": self.last_usage["input_tokens"] + result.usage["input_tokens"],
                    "output_tokens": self.last_usage["output_tokens"] + result.usage["output_tokens"],
                    "calls": self.last_usage["calls"] + 1,
                }
                if isinstance(result.content, dict):
                    return result.content
            except ProviderError as exc:
                log.error("%s: provider error (attempt %d): %s", self.name, attempt, exc)
                if "timeout" in str(exc).lower():
                    return None
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: LLM attempt %d failed: %s", self.name, attempt, exc)
        return None

    def _validate(self, report: dict) -> bool:
        if not isinstance(report, dict):
            return False
        required = self.schema.get("required", [])
        props = self.schema.get("properties", {})
        for key in required:
            if key not in report or report[key] in (None, ""):
                return False
        # confidence may appear as `confidence` or `confidence_score`
        conf_key = next(
            (k for k in ("confidence", "confidence_score") if k in report), None
        )
        if conf_key is None:
            return False
        try:
            conf = float(report[conf_key])
        except (TypeError, ValueError):
            return False
        if not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
            return False
        for key, spec in props.items():
            if key in report and not self._matches_schema(report[key], spec):
                return False
        # deterministic identity regardless of what the model wrote
        report["agent"] = self.name
        return True

    @staticmethod
    def _matches_schema(value, spec: dict) -> bool:
        if spec.get("enum") and value not in spec["enum"]:
            return False
        kind = spec.get("type")
        if kind == "string":
            return isinstance(value, str)
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if kind == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if kind == "array":
            return isinstance(value, list) and all(
                BaseAgent._matches_schema(item, spec.get("items", {})) for item in value
            )
        if kind == "object":
            if not isinstance(value, dict):
                return False
            if any(key not in value for key in spec.get("required", [])):
                return False
            return all(
                key not in value or BaseAgent._matches_schema(value[key], child)
                for key, child in spec.get("properties", {}).items()
            )
        return True


def _enum(values: list[str]) -> dict:
    return {"type": "string", "enum": values}


def _report_schema(extra_props: dict, required_extra: list[str]) -> dict:
    props = {
        "agent": {"type": "string"},
        "symbol": {"type": "string"},
        "confidence": {"type": "number"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    }
    props.update(extra_props)
    return {
        "type": "object",
        "properties": props,
        "required": ["agent", "symbol", "confidence", "key_points", *required_extra],
    }


COMMON_RULES = (
    "You are an expert options-trading analysis agent. "
    "You receive PRE-COMPUTED numeric metrics from a deterministic quant engine. "
    "NEVER recompute numbers; interpret them. Be concise and factual. "
    "Answer with a single JSON object matching the given schema exactly. No prose."
)
