"""Context Manager: compile news/event report for the chief."""
from agents.base_agent import BaseAgent, _enum


class ContextManager(BaseAgent):
    name = "ContextManager"
    model_tier = "light"
    system_prompt = (
        "You are an expert trading context analyst. Compile the news/event report "
        "into a decision-ready summary. Flag earnings_warning true when an earnings "
        "event is close enough to threaten option buyers via IV crush. Answer with a "
        "single JSON object matching the schema exactly. No prose."
    )
    schema = {
        "type": "object",
        "properties": {
            "manager": {"type": "string"},
            "symbol": {"type": "string"},
            "overall_event_risk": _enum(["HIGH", "MEDIUM", "LOW"]),
            "summary": {"type": "string"},
            "confidence_score": {"type": "number"},
            "earnings_warning": {"type": "boolean"},
        },
        "required": ["manager", "symbol", "overall_event_risk", "summary",
                     "confidence_score", "earnings_warning"],
    }

    def fallback(self) -> dict:
        return {
            "manager": self.name,
            "symbol": "",
            "overall_event_risk": "MEDIUM",
            "summary": "fallback compile (LLM unavailable/invalid)",
            "confidence_score": 0.15,
            "earnings_warning": True,
        }
