"""NewsEarningsAgent: event risk & sentiment from headlines + earnings estimate."""
from agents.base_agent import COMMON_RULES, BaseAgent, _enum, _report_schema


class NewsEarningsAgent(BaseAgent):
    name = "NewsEarningsAgent"
    model_tier = "light"
    system_prompt = (
        COMMON_RULES +
        " ROLE: assess event risk for option pricing: earnings proximity "
        "(IV crush danger for long premium when earnings_proximity_days <= 10), "
        "major news sentiment. event_risk CRITICAL|HIGH|MEDIUM|LOW, sentiment "
        "POSITIVE|NEGATIVE|NEUTRAL."
    )
    schema = _report_schema(
        {
            "event_risk": _enum(["CRITICAL", "HIGH", "MEDIUM", "LOW"]),
            "sentiment": _enum(["POSITIVE", "NEGATIVE", "NEUTRAL"]),
            "earnings_warning": {"type": "boolean"},
        },
        ["event_risk", "sentiment", "earnings_warning"],
    )

    def fallback(self) -> dict:
        return {
            "agent": self.name,
            "symbol": "",
            "event_risk": "MEDIUM",
            "sentiment": "NEUTRAL",
            "earnings_warning": True,
            "confidence": 0.15,
            "key_points": ["fallback: assume elevated caution (LLM unavailable/invalid)"],
            "risk_flags": ["degraded news analysis"],
        }
