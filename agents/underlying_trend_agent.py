"""UnderlyingTrendAgent: directional bias & trend strength."""
from agents.base_agent import COMMON_RULES, BaseAgent, _enum, _report_schema


class UnderlyingTrendAgent(BaseAgent):
    name = "UnderlyingTrendAgent"
    model_tier = "light"
    system_prompt = (
        COMMON_RULES +
        " ROLE: judge direction & strength of the underlying from z_score_20d, "
        "price vs sma_20/sma_50, momentum_20d_pct. bias BULLISH|BEARISH|NEUTRAL, "
        "trend_strength STRONG|MODERATE|WEAK."
    )
    schema = _report_schema(
        {
            "bias": _enum(["BULLISH", "BEARISH", "NEUTRAL"]),
            "trend_strength": _enum(["STRONG", "MODERATE", "WEAK"]),
        },
        ["bias", "trend_strength"],
    )

    def fallback(self) -> dict:
        return {
            "agent": self.name,
            "symbol": "",
            "bias": "NEUTRAL",
            "trend_strength": "WEAK",
            "confidence": 0.15,
            "key_points": ["fallback: neutral (LLM unavailable/invalid)"],
            "risk_flags": ["degraded trend analysis"],
        }
