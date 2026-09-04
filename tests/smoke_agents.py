"""Smoke test the LLM agent chain end-to-end for one symbol."""
import json
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from agents.context_manager import ContextManager  # noqa: E402
from agents.news_earnings_agent import NewsEarningsAgent  # noqa: E402
from agents.strategy_decision_agent import StrategyDecisionAgent  # noqa: E402
from llm import provider_available  # noqa: E402
from agents.technical_manager import TechnicalManager  # noqa: E402
from agents.underlying_trend_agent import UnderlyingTrendAgent  # noqa: E402
from agents.volatility_agent import VolatilityAgent  # noqa: E402
from data_engine.option_data import candidate_strikes, fetch_chain  # noqa: E402
from data_engine.stock_data import get_spot_price  # noqa: E402
from quant_engine.engine import build_quant_report  # noqa: E402


def main() -> None:
    symbol = "SPY"
    print(f"provider_available={provider_available()}")
    t0 = time.time()
    qr = build_quant_report(symbol)
    spot = get_spot_price(symbol)

    trend = UnderlyingTrendAgent().run(qr)
    vol = VolatilityAgent().run(qr)
    news = NewsEarningsAgent().run(
        {
            "symbol": symbol,
            "headlines": [h["headline"] for h in __import__(
                "data_engine.news_data", fromlist=["x"]
            ).get_recent_news(symbol)],
            "earnings": qr["earnings"],
        }
    )
    tech = TechnicalManager().run({"quant": qr, "trend": trend, "volatility": vol})
    ctx = ContextManager().run({"news_report": news, "earnings": qr["earnings"]})
    print(f"[timing] analysis chain took {time.time()-t0:.1f}s")

    chain = fetch_chain(symbol)
    expiry = qr["option_chain_summary"]["expiry_used"]
    candidates = candidate_strikes(chain, spot, expiry, n_each_side=3)

    proposal_payload = {
        "symbol": symbol,
        "technical_report": tech,
        "context_report": ctx,
        "spot_price": spot,
        "candidate_expiry": expiry,
        "candidate_calls": candidates["calls"],
        "candidate_puts": candidates["puts"],
        "buying_power": 400000,
    }
    t1 = time.time()
    proposal = StrategyDecisionAgent().run(proposal_payload)
    print(f"[timing] chief decision took {time.time()-t1:.1f}s")

    for name, rep in [
        ("TREND", trend),
        ("VOL", vol),
        ("NEWS", news),
        ("TECH_MGR", tech),
        ("CTX_MGR", ctx),
        ("PROPOSAL", proposal),
    ]:
        print(f"\n=== {name} ===")
        print(json.dumps(rep, indent=1)[:1200])


if __name__ == "__main__":
    main()
