"""VolatilityAgent: volatility regime & premium buy/sell bias."""
from agents.base_agent import COMMON_RULES, BaseAgent, _enum, _report_schema


class VolatilityAgent(BaseAgent):
    name = "VolatilityAgent"
    model_tier = "light"
    system_prompt = (
        COMMON_RULES +
        " ROLE: interpret the volatility block. iv_rank_proxy_hv_based >=60 => HIGH_IV "
        "(premium expensive: favor selling premium), <=40 => LOW_IV (favor buying), else NEUTRAL. "
        "hv_iv_spread >0 means IV richer than realized (sell premium tilt); <0 the reverse. "
        "Also weigh expected_move vs recent realized range."
    )
    schema = _report_schema(
        {
            "volatility_regime": _enum(["HIGH_IV", "LOW_IV", "NEUTRAL"]),
            "premium_bias": _enum(["SELL_PREMIUM", "BUY_PREMIUM", "NEUTRAL"]),
        },
        ["volatility_regime", "premium_bias"],
    )

    def fallback(self) -> dict:
        return {
            "agent": self.name,
            "symbol": "",
            "volatility_regime": "NEUTRAL",
            "premium_bias": "NEUTRAL",
            "confidence": 0.15,
            "key_points": ["fallback: neutral (LLM unavailable/invalid)"],
            "risk_flags": ["degraded volatility analysis"],
        }
