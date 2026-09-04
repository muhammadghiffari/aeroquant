"""Technical Manager: compile trend + volatility reports."""
from agents.base_agent import BaseAgent, _enum


class TechnicalManager(BaseAgent):
    name = "TechnicalManager"
    model_tier = "heavy"
    system_prompt = (
        "You are an expert trading technical manager. Compile the underlying-trend "
        "report and the volatility report into one decision-ready summary. State "
        "whether the two signals align (FULL), partially align (PARTIAL) or clash "
        "(CLASH). Answer with a single JSON object matching the schema exactly. No prose."
    )
    schema = {
        "type": "object",
        "properties": {
            "manager": {"type": "string"},
            "symbol": {"type": "string"},
            "overall_bias": {"type": "string"},
            "volatility_regime": {"type": "string"},
            "alignment": _enum(["FULL", "PARTIAL", "CLASH"]),
            "summary": {"type": "string"},
            "confidence_score": {"type": "number"},
            "critical_risks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["manager", "symbol", "overall_bias", "volatility_regime",
                     "alignment", "summary", "confidence_score"],
    }

    def fallback(self) -> dict:
        return {
            "manager": self.name,
            "symbol": "",
            "overall_bias": "NEUTRAL",
            "volatility_regime": "NEUTRAL",
            "alignment": "PARTIAL",
            "summary": "fallback compile (LLM unavailable/invalid)",
            "confidence_score": 0.15,
            "critical_risks": ["degraded technical compilation"],
        }
