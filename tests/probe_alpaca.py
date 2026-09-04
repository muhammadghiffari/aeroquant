"""Day-0 spike: verify alpaca-py capabilities against the paper account.

Checks: clock, option chain (greeks/IV), contracts endpoint (OI),
stock bars, news. Run: python -m tests.probe_alpaca
"""
import json
import re
from datetime import date, timedelta

OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str) -> dict:
    """Parse OCC option symbol: AAPL260919C00220000."""
    m = OCC_RE.match(symbol)
    if not m:
        return {"root": symbol, "expiry": None, "type": None, "strike": None}
    root, ymd, cp, strike = m.groups()
    expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
    return {
        "root": root,
        "expiry": expiry,
        "type": "call" if cp == "C" else "put",
        "strike": int(strike) / 1000.0,
    }

from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    NewsRequest,
    OptionChainRequest,
    StockBarsRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

import config


def main() -> None:
    key = config.ALPACA_PAPER_API_KEY
    secret = config.ALPACA_PAPER_SECRET_KEY

    trade = TradingClient(key, secret, paper=True)
    acct = trade.get_account()
    print(f"[account] status={acct.status} options_level={acct.options_trading_level}")

    clock = trade.get_clock()
    print(f"[clock] is_open={clock.is_open} next_open={clock.next_open}")

    opt = OptionHistoricalDataClient(key, secret)
    chain = opt.get_option_chain(OptionChainRequest(underlying_symbol="AAPL"))
    snaps = list(chain.values()) if isinstance(chain, dict) else list(chain)
    print(f"[chain] contracts={len(snaps)} sample:")
    if snaps:
        print(f"[chain] fields={[f for f in snaps[0].model_dump().keys()]}")
    for s in snaps[:4]:
        meta = parse_occ(s.symbol)
        greeks = s.greeks
        iv = float(s.implied_volatility) if s.implied_volatility else None
        d = {
            "symbol": s.symbol,
            "expiry": meta["expiry"],
            "type": meta["type"],
            "strike": meta["strike"],
            "iv": round(iv, 4) if iv else None,
            "delta": round(float(greeks.delta), 3) if greeks else None,
            "bid": float(s.latest_quote.bid_price) if s.latest_quote else None,
            "ask": float(s.latest_quote.ask_price) if s.latest_quote else None,
        }
        print("  ", json.dumps(d))

    try:
        contracts = trade.get_option_contracts(
            GetOptionContractsRequest(
                underlying_symbols=["AAPL"], status="active", limit=5
            )
        )
        for c in contracts.option_contracts[:5]:
            print(
                f"[contracts] {c.symbol} oi={c.open_interest} "
                f"oi_date={c.open_interest_date}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[contracts] ERROR: {exc}")

    stock = StockHistoricalDataClient(key, secret)
    bars = stock.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols="AAPL",
            timeframe=TimeFrame.Day,
            start=date.today() - timedelta(days=40),
        )
    )
    df = bars.df.reset_index()
    print(f"[stock_bars] rows={len(df)} cols={sorted(df.columns.tolist())}")
    if len(df):
        last = df.iloc[-1]
        print(f"[stock_bars] last close={last['close']} at {last['timestamp']}")

    news = NewsClient(key, secret)
    items = news.get_news(NewsRequest(symbols="AAPL", limit=2))
    for n in items.data.get("news", []):
        print(f"[news] {n.created_at:%Y-%m-%d} | {n.headline[:70]}")


if __name__ == "__main__":
    main()
