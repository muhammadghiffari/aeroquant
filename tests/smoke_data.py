"""Smoke test data engine. Run: python -m tests.smoke_data"""
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from data_engine.news_data import news_context  # noqa: E402
from data_engine.option_data import build_chain_summary, fetch_chain  # noqa: E402
from data_engine.stock_data import get_daily_bars, get_spot_price  # noqa: E402


def main() -> None:
    symbol = "SPY"
    spot = get_spot_price(symbol)
    print(f"{symbol} spot: {spot}")
    bars = get_daily_bars(symbol, 400)
    print(f"bars: {len(bars)} rows")

    chain = fetch_chain(symbol, max_spread_pct=0.25)
    print(f"chain (spread<=25%): {len(chain)} contracts")
    s = build_chain_summary(chain, spot)
    slim = {
        k: v
        for k, v in s.items()
        if k not in ("atm_call", "atm_put", "put_25delta", "call_25delta")
    }
    print(json.dumps(slim, indent=1))
    if s.get("atm_call"):
        print("ATM call:", json.dumps(s["atm_call"]))
        print("ATM put :", json.dumps(s["atm_put"]))
    if s.get("skew_put_call_25delta") is not None:
        print(f"skew 25d: {s['skew_put_call_25delta']}")

    nc = news_context(symbol)
    print(f"news items: {len(nc['headlines'])}")
    for h in nc["headlines"][:3]:
        print("  -", h["headline"][:80])
    print("earnings est:", json.dumps(nc["earnings"]))


if __name__ == "__main__":
    main()
