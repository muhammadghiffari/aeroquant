"""
model_gateway.py — Multi-provider LLM gateway with automatic failover.

Replaces the single-provider `AnthropicModelGateway` sketch from the Architecture
Discussion Pack §10.2 with a multi-provider version, same call shape, so it drops
into the existing LangGraph nodes without changing node code.

PROVIDERS (all standalone, paid, ToS-compliant API keys — see PRD §5.3):
  1. Anthropic       — primary. Best structured-output reliability (native tool
                        use), used first for every role.
  2. Featherless      — confirmed secondary/fallback. OpenAI-compatible, huge
                        open-weight catalog. Qwen3-32B is the active default
                        because Featherless documents *native* tool calling for
                        it — other model families on Featherless can return tool
                        calls as plain text, which breaks structured-output
                        parsing silently. (GLM-4.7-Flash/GLM-5.2 are a candidate
                        swap under evaluation, not yet adopted — see PRD §5.3.)

BytePlus Ark is NOT an active provider. PRD §5.3/§12 explicitly resolved this:
"no partner/subscription evidence found; not adopted... with the BytePlus Ark
candidate removed [from the fallback chain]." `_byteplus_ark()` below is kept
only as inert reference code (dead code, per explicit team decision) — it is
not called anywhere in `_build_role_chains()`. Do NOT wire it back into an
active chain without a new, explicit team decision (CLAUDE.md "What NOT to do").

NEVER add a fourth candidate that points at a Claude Code / ChatGPT-Plus OAuth
token, or any third-party "custom provider" proxy URL claiming to route around
official billing. That's a closed decision (PRD §5.3) — it doesn't belong in
this file even as a low-priority fallback.

ENV VARS NEEDED:
  ANTHROPIC_API_KEY   (per RUNBOOK §8: for a cost-free local smoke test of the
                       Featherless fallback path, set this to a present-but-
                       invalid string, e.g. sk-ant-invalid-for-testing — do NOT
                       leave it unset, since _build_role_chains() constructs
                       every chain eagerly and an unset key raises KeyError
                       before any fallback logic runs. This is a local-dev-only
                       trick — the real scored run still requires a real key,
                       Anthropic is the documented primary, PRD §5.3.)
  FEATHERLESS_API_KEY

  BYTEPLUS_ARK_API_KEY, BYTEPLUS_*_ENDPOINT_ID are intentionally NOT needed —
  do not add them to .env (see _env.example).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal, TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

logger = logging.getLogger("aeroquant.model_gateway")

T = TypeVar("T", bound=BaseModel)

# Per-candidate timeout. This is the real ceiling on "how long before we give
# up on a candidate and move to the next one" — keep it short. A clean 429
# comes back almost instantly regardless of this number; this only matters
# for a candidate that goes silent instead of erroring cleanly.
_CANDIDATE_TIMEOUT_S = 8


# ---------------------------------------------------------------------------
# 1. Provider client builders
# ---------------------------------------------------------------------------

def _anthropic(model: str) -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=_CANDIDATE_TIMEOUT_S,
        max_retries=0,  # retries happen at the fallback-chain level below, not inside one client
    )


def _byteplus_ark(model_or_endpoint_id: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_or_endpoint_id,
        base_url="https://ark.ap-southeast.bytepluses.com/api/v3",
        api_key=os.environ["BYTEPLUS_ARK_API_KEY"],
        timeout=_CANDIDATE_TIMEOUT_S,
        max_retries=0,
    )


def _featherless(model: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,  # Hugging-Face-style "owner/model" id
        base_url="https://api.featherless.ai/v1",
        api_key=os.environ["FEATHERLESS_API_KEY"],
        timeout=_CANDIDATE_TIMEOUT_S,
        max_retries=0,
    )


# ---------------------------------------------------------------------------
# 2. Circuit breaker — skip a provider that's been failing repeatedly instead
#    of paying its timeout every single cycle.
# ---------------------------------------------------------------------------

@dataclass
class _ProviderHealth:
    consecutive_failures: int = 0
    open_until: float = 0.0  # epoch seconds; while now < open_until, treat as down


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state: dict[str, _ProviderHealth] = {}

    def is_open(self, provider_name: str) -> bool:
        h = self._state.get(provider_name)
        return bool(h and time.time() < h.open_until)

    def record_success(self, provider_name: str) -> None:
        self._state[provider_name] = _ProviderHealth()

    def record_failure(self, provider_name: str) -> None:
        h = self._state.setdefault(provider_name, _ProviderHealth())
        h.consecutive_failures += 1
        if h.consecutive_failures >= self.failure_threshold:
            h.open_until = time.time() + self.cooldown_seconds
            logger.warning(
                "circuit_breaker_open provider=%s cooldown_s=%s failures=%s",
                provider_name, self.cooldown_seconds, h.consecutive_failures,
            )


_breaker = CircuitBreaker()


# ---------------------------------------------------------------------------
# 3. Role-policy fallback chains — matches PRD §5.2/§5.3's fast_analysis /
#    strong_reasoning / critic policies. Anthropic first in every chain
#    (best structured-output reliability); the two paid backups follow.
# ---------------------------------------------------------------------------

def _build_role_chains() -> dict[str, list[tuple[str, object]]]:
    # BytePlus intentionally excluded from every chain — PRD §5.3/§12: "not
    # adopted... with the BytePlus Ark candidate removed, Featherless promoted
    # to the sole non-Anthropic candidate per chain." _byteplus_ark() stays
    # defined above as dead code only; do not call it here without a new,
    # explicit team decision.
    return {
        "fast_analysis": [
            ("anthropic-haiku-4.5", _anthropic("claude-haiku-4-5-20251001")),
            ("featherless-qwen3-32b", _featherless("Qwen/Qwen3-32B")),
        ],
        "strong_reasoning": [
            ("anthropic-sonnet-5", _anthropic("claude-sonnet-5")),
            ("featherless-qwen3-32b", _featherless("Qwen/Qwen3-32B")),
        ],
        "critic": [
            ("anthropic-sonnet-5", _anthropic("claude-sonnet-5")),
            ("featherless-qwen3-32b", _featherless("Qwen/Qwen3-32B")),
        ],
    }


# ---------------------------------------------------------------------------
# 4. The gateway
# ---------------------------------------------------------------------------

class ModelGateway:
    """
    Drop-in replacement for the Architecture Pack §10.2 AnthropicModelGateway.
    Same call shape (`generate(role=..., policy=..., messages=..., response_model=...)`),
    now backed by an ordered multi-provider fallback chain per policy instead
    of a single Anthropic client.
    """

    def __init__(self) -> None:
        self._chains = _build_role_chains()

    def generate(
        self,
        *,
        role: str,
        policy: Literal["fast_analysis", "strong_reasoning", "critic"],
        messages: list,
        response_model: type[T],
        correlation_id: str,
    ) -> tuple[T, str]:
        """
        Returns (parsed_response, provider_name_that_actually_served_it) —
        keep the provider name in your AgentReport/telemetry (Architecture
        Pack §10.5 already asks for "provider, model ID" per call; this is
        that field, now meaningfully variable instead of always "anthropic").

        Tries each candidate in order. Any exception (429, auth error,
        timeout, malformed/unparseable structured output) moves immediately
        to the next candidate — no sleep-and-retry on the same one first.
        For a clean rate-limit rejection this is genuinely sub-second, since
        a 429 is an instant HTTP response, not something worth waiting out.
        """
        last_error: Exception | None = None
        for provider_name, client in self._chains[policy]:
            if _breaker.is_open(provider_name):
                logger.info("skip_open_breaker provider=%s role=%s", provider_name, role)
                continue
            try:
                structured_client = client.with_structured_output(response_model, include_raw=True)
                result = structured_client.invoke(
                    messages,
                    config={"run_name": role, "tags": [correlation_id, provider_name]},
                )
                parsed = result["parsed"]
                if parsed is None:
                    raise ValueError(f"{provider_name} returned unparseable structured output")
                _breaker.record_success(provider_name)
                logger.info(
                    "model_call_ok role=%s provider=%s correlation_id=%s",
                    role, provider_name, correlation_id,
                )
                return parsed, provider_name
            except Exception as exc:  # noqa: BLE001 — intentionally broad: any failure -> next candidate
                _breaker.record_failure(provider_name)
                logger.warning(
                    "model_call_failed role=%s provider=%s correlation_id=%s error=%s",
                    role, provider_name, correlation_id, exc,
                )
                last_error = exc
                continue

        raise RuntimeError(
            f"All providers exhausted for role={role} policy={policy} "
            f"correlation_id={correlation_id}: {last_error}"
        )


if __name__ == "__main__":
    # Smoke test — run this on Day 0 exactly like the Anthropic-only version
    # in RUNBOOK.md step 4, but now confirming all three providers per policy.
    import logging as _logging
    from pydantic import Field

    _logging.basicConfig(level=_logging.INFO)

    class _PingReply(BaseModel):
        reply: str = Field(description="Should be exactly OK")

    gateway = ModelGateway()
    for policy in ("fast_analysis", "strong_reasoning", "critic"):
        parsed, provider = gateway.generate(
            role=f"smoke_test_{policy}",
            policy=policy,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            response_model=_PingReply,
            correlation_id="smoke-test",
        )
        print(f"{policy:>16}: served by {provider:<28} -> {parsed.reply}")
