"""News + earnings proximity.

Alpaca has no earnings-calendar endpoint (even official SDK examples use a
quarter-pattern heuristic), so we combine:
1. Recent headlines from Alpaca News API.
2. Earnings proximity estimate from the standard quarterly reporting pattern,
   clearly labeled as heuristic in the output.
"""
import logging
from datetime import date, datetime

from alpaca.data.requests import NewsRequest

from data_engine import alpaca_client

log = logging.getLogger(__name__)

# Typical reporting month per calendar quarter (mid-month), like Alpaca's own
# examples use. Labeled heuristic -- not exact dates.
_QUARTER_MONTHS = {1: 4, 2: 4, 3: 7, 4: 7, 5: 7, 6: 10, 7: 10, 8: 10, 9: 1, 10: 1, 11: 1, 12: 4}


def get_recent_news(symbol: str, limit: int = 8) -> list[dict]:
    req = NewsRequest(symbols=symbol.upper(), limit=limit)
    resp = alpaca_client.safe("get_news", alpaca_client.news_client().get_news, req)
    items = resp.data.get("news", [])
    out = []
    for n in items:
        out.append(
            {
                "headline": n.headline,
                "summary": (n.summary or "")[:280],
                "source": n.source,
                "created_at": n.created_at.isoformat(),
            }
        )
    return out


def estimate_earnings_proximity(symbol: str, today: date = None) -> dict:
    """Heuristic days-to-next-earnings from the quarterly pattern."""
    today = today or date.today()
    target_month = _QUARTER_MONTHS[today.month]
    year = today.year
    # mid-month assumption: day ~15
    candidate = date(year, target_month, 15)
    if candidate < today:
        candidate = date(year + 1, target_month, 15)
    days = (candidate - today).days
    return {
        "estimated_next_earnings": candidate.isoformat(),
        "earnings_proximity_days": days,
        "method": "heuristic_quarter_pattern",
    }


def news_context(symbol: str) -> dict:
    return {
        "symbol": symbol.upper(),
        "headlines": get_recent_news(symbol),
        "earnings": estimate_earnings_proximity(symbol),
    }
