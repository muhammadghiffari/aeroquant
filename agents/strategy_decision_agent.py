"""Strategy Decision Agent -- Chief Supervisor.

Chooses ONE concrete strategy from real candidate contracts provided in the
payload (it may only reference contract symbols that exist there).
"""
from agents.base_agent import BaseAgent, _enum


def validate_candidate_choice(proposal: dict, candidates: list[dict]) -> bool:
    """Require the LLM proposal to reference one exact Quant candidate."""
    if proposal.get("strategy_type") == "WAIT":
        return proposal.get("candidate_id") in {None, "", "NONE"} and not proposal.get("legs")
    candidate_id = proposal.get("candidate_id")
    candidate = next((item for item in candidates if item.get("candidate_id") == candidate_id), None)
    if candidate is None:
        return False
    if proposal.get("strategy_type") != candidate.get("strategy_type"):
        return False
    legs = proposal.get("legs") or []
    if len(legs) != 1:
        return False
    leg = legs[0]
    return (
        leg.get("action") == "BUY"
        and leg.get("symbol") == candidate.get("symbol")
        and leg.get("qty") == 1
    )


class StrategyDecisionAgent(BaseAgent):
    name = "StrategyDecisionAgent"
    model_tier = "heavy"
    system_prompt = (
        "You are the Strategy Decision Agent supervising a deterministic Quant-first "
        "single-leg momentum system. Quant is authoritative. Choose WAIT when its "
        "entry gate is false, data is degraded, candidates are absent, or news risk "
        "is critical. If quant_gate.analysis_only is true, you may choose one "
        "shadow candidate for analysis, but the result is SHADOW_ONLY and never "
        "reaches the executor. A GREEN_PROXY entry_confidence is based on real "
        "1H stock bars; contract_confidence may still be WAIT_DATA, which is an "
        "explicit limitation rather than a contradiction. For GREEN_PROXY, use "
        "live option bid/ask and bid_size/ask_size only as current quote and activity "
        "context; never wait for historical option quotes. For UNDERLYING_HISTORY_PROXY, "
        "entry_actionable=true and the candidate whitelist are the effective entry gate; "
        "do not turn advisory WAIT_SEE or WAIT_DATA fields into WAIT by themselves. "
        "Never create a trade from intuition.\n"
        "Inputs include a Quant-approved or explicitly shadow-only candidate list. A trade candidate_id "
        "and option symbol MUST be copied exactly from that whitelist. Use candidate_id "
        "NONE and no legs for WAIT.\n"
        "Strategies (construction rules are STRICT -- violations get rejected):\n"
        "- LONG_CALL: BUY exactly one call option for a strong bullish directional bias.\n"
        "- LONG_PUT: BUY exactly one put option for a strong bearish directional bias.\n"
        "- WAIT: choose this when the directional evidence is insufficient.\n"
        "Only LONG_CALL and LONG_PUT are executable.\n"
        "Rules:\n"
        "1) A trade MUST contain exactly one BUY leg using symbol copied EXACTLY from candidate_id; qty is ALWAYS 1.\n"
        "2) Never change Quant direction, DTE, expiry, strike, delta, or risk budget.\n"
        "3) Critical event risk or missing candidate data means WAIT.\n"
        "4) Provide a factual rationale tied to Quant and grounded news evidence.\n"
        "5) You never execute orders. "
        "Output a single JSON object matching the schema exactly. No prose."
    )
    schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "symbol": {"type": "string"},
            "candidate_id": {"type": "string"},
            "strategy_type": _enum(
                ["WAIT", "LONG_CALL", "LONG_PUT"]
            ),
            "rationale": {"type": "string"},
            "legs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": _enum(["BUY"]),
                        "symbol": {"type": "string"},
                        "qty": {"type": "integer"},
                    },
                    "required": ["action", "symbol", "qty"],
                },
            },
            "estimated_credit_or_debit": {"type": "number"},
            "confidence": {"type": "number"},
        },
         "required": ["agent", "symbol", "candidate_id", "strategy_type", "rationale", "legs", "confidence"],
    }

    def fallback(self) -> dict:
        return {
            "agent": self.name,
            "symbol": "",
            "candidate_id": "NONE",
            "strategy_type": "WAIT",
            "rationale": "fallback: no-trade (LLM unavailable or output invalid)",
            "legs": [],
            "confidence": 0.1,
        }
