"""Option chain data: normalize snapshots, parse OCC symbols, build summaries.

Verified against alpaca-py 0.44 / data API:
- get_option_chain returns {contract_symbol: OptionsSnapshot} where snapshot has
  latest_trade, latest_quote, implied_volatility, greeks -- but NO contract
  metadata (strike/expiry/type), so we parse the OCC symbol instead.
- IV/greeks are None on illiquid contracts -> filter to contracts with quotes+IV.
- Open interest comes from the Trading API contracts endpoint and is sparse;
  liquidity is judged primarily on bid/ask spread %.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from alpaca.data.requests import OptionChainRequest

from data_engine import alpaca_client
from data_engine.stock_data import get_spot_price

log = logging.getLogger(__name__)

OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

MIN_VALID_DELTA = 0.03
MAX_VALID_IV = 3.0  # 300%


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    expiry: str          # YYYY-MM-DD
    opt_type: str        # "call" | "put"
    strike: float
    bid: float
    ask: float
    mid: float
    spread_pct: float    # (ask-bid)/mid
    iv: Optional[float]
    delta: Optional[float]
    theta: Optional[float] = None
    open_interest: Optional[int] = None
    dte: int = 0
    volume: Optional[int] = None
    last_trade_size: Optional[int] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    quote_timestamp: Optional[str] = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def parse_occ(symbol: str):
    """Parse OCC symbol like AAPL260919C00220000 -> (expiry_iso, type, strike)."""
    m = OCC_RE.match(symbol)
    if not m:
        return None
    _, ymd, cp, strike = m.groups()
    return (
        f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}",
        "call" if cp == "C" else "put",
        int(strike) / 1000.0,
    )


def _dte(expiry_iso: str, today: date) -> int:
    exp = datetime.strptime(expiry_iso, "%Y-%m-%d").date()
    return max((exp - today).days, 0)


def is_entry_expiry_allowed(expiry_iso: str) -> bool:
    """Allow contracts that remain alive until the mandatory final close.

    American-style options can be sold before expiry.  The final-close date is
    an exit deadline, so an entry must expire after it rather than before it.
    """
    import config

    return date.fromisoformat(expiry_iso) > config.FINAL_CLOSE_DATE


def fetch_chain(
    symbol: str,
    min_dte: int = None,
    max_dte: int = None,
    max_spread_pct: float = None,
) -> list[OptionContract]:
    """Fetch + normalize the full chain for one underlying.

    Keeps only contracts with a two-sided quote; IV/delta may still be None.
    """
    import config

    min_dte = config.MIN_DTE if min_dte is None else min_dte
    max_dte = config.MAX_DTE if max_dte is None else max_dte
    max_spread_pct = (
        config.MAX_BID_ASK_SPREAD_PCT if max_spread_pct is None else max_spread_pct
    )

    req = OptionChainRequest(underlying_symbol=symbol.upper())
    raw = alpaca_client.safe(
        "get_option_chain", alpaca_client.option_data_client().get_option_chain, req
    )
    snaps = raw.values() if isinstance(raw, dict) else raw

    today = config.market_date()
    out: list[OptionContract] = []
    for s in snaps:
        occ = parse_occ(s.symbol)
        if not occ:
            continue
        expiry, opt_type, strike = occ
        if not is_entry_expiry_allowed(expiry):
            continue
        d = _dte(expiry, today)
        if d < min_dte or d > max_dte:
            continue
        q = s.latest_quote
        if q is None or q.bid_price is None or q.ask_price is None:
            continue
        bid, ask = float(q.bid_price), float(q.ask_price)
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid if mid > 0 else 1.0
        if spread_pct > max_spread_pct:
            continue
        iv = float(s.implied_volatility) if s.implied_volatility else None
        if iv is not None and (iv <= 0 or iv > MAX_VALID_IV):
            iv = None
        greeks = s.greeks
        delta = float(greeks.delta) if greeks and greeks.delta is not None else None
        theta = float(greeks.theta) if greeks and greeks.theta is not None else None
        daily_bar = getattr(s, "daily_bar", None)
        volume = getattr(daily_bar, "volume", None) if daily_bar else None
        last_trade = getattr(s, "latest_trade", None)
        last_trade_size = getattr(last_trade, "size", None) if last_trade else None
        open_interest = getattr(s, "open_interest", None)
        quote_timestamp = getattr(q, "timestamp", None)
        if quote_timestamp is not None and hasattr(quote_timestamp, "isoformat"):
            quote_timestamp = quote_timestamp.isoformat()
        if delta is not None and abs(delta) < MIN_VALID_DELTA:
            delta = None

        out.append(
            OptionContract(
                symbol=s.symbol,
                underlying=symbol.upper(),
                expiry=expiry,
                opt_type=opt_type,
                strike=strike,
                bid=round(bid, 2),
                ask=round(ask, 2),
                mid=round(mid, 2),
                spread_pct=round(spread_pct, 4),
                iv=round(iv, 4) if iv else None,
                delta=round(delta, 4) if delta else None,
                theta=round(theta, 4) if theta else None,
                dte=d,
                volume=int(volume) if volume is not None else None,
                last_trade_size=int(last_trade_size) if last_trade_size is not None else None,
                bid_size=int(q.bid_size) if getattr(q, "bid_size", None) is not None else None,
                ask_size=int(q.ask_size) if getattr(q, "ask_size", None) is not None else None,
                open_interest=int(open_interest) if open_interest is not None else None,
                quote_timestamp=str(quote_timestamp) if quote_timestamp else None,
            )
        )
    log.info("chain %s: %d tradable contracts (DTE %d-%d)", symbol, len(out), min_dte, max_dte)
    return out


def atm_contracts(chain: list[OptionContract], spot: float, expiry: str):
    calls = [c for c in chain if c.expiry == expiry and c.opt_type == "call" and c.iv]
    puts = [c for c in chain if c.expiry == expiry and c.opt_type == "put" and c.iv]
    call = min(calls, key=lambda c: abs(c.strike - spot)) if calls else None
    put = min(puts, key=lambda c: abs(c.strike - spot)) if puts else None
    return call, put


def nearest_expiry(chain: list[OptionContract], target_dte: int = 7) -> Optional[str]:
    import config

    expiries = sorted({c.expiry for c in chain})
    if not expiries:
        return None
    best, best_diff = None, 10**9
    today = config.market_date()
    for e in expiries:
        diff = abs(_dte(e, today) - target_dte)
        if diff < best_diff:
            best, best_diff = e, diff
    return best


def resolve_leg(chain: list[OptionContract], opt_type: str, strike: float, expiry: str) -> Optional[OptionContract]:
    """Find an exact candidate contract; never rewrite a proposal's strike."""
    same_exp = [
        c for c in chain if c.expiry == expiry and c.opt_type == opt_type.lower()
    ]
    if not same_exp:
        return None
    exact = [c for c in same_exp if abs(c.strike - strike) < 0.001]
    if exact:
        return exact[0]
    return None


def build_chain_summary(chain: list[OptionContract], spot: float) -> dict:
    """Compact numeric summary that feeds the Quant Engine report."""
    expiry = nearest_expiry(chain)
    summary: dict = {"expiries_available": sorted({c.expiry for c in chain})[:8]}
    if not expiry:
        summary["error"] = "no valid expiries in DTE range"
        return summary

    call, put = atm_contracts(chain, spot, expiry)
    summary["expiry_used"] = expiry

    atm_ivs = []
    deltas_c, deltas_p = [], []
    spreads = []
    volumes = []
    trade_sizes = []
    quote_sizes = []
    for c in chain:
        if c.expiry != expiry:
            continue
        if c.iv:
            atm_ivs.append(c.iv)
        if c.delta:
            deltas_c.append(c.delta) if c.opt_type == "call" else deltas_p.append(c.delta)
        spreads.append(c.spread_pct)
        if c.volume is not None:
            volumes.append(c.volume)
        if c.last_trade_size is not None:
            trade_sizes.append(c.last_trade_size)
        if c.bid_size is not None and c.ask_size is not None:
            quote_sizes.append(c.bid_size + c.ask_size)

    summary.update(
        {
            "atm_call": call.as_dict() if call else None,
            "atm_put": put.as_dict() if put else None,
            "median_iv_expiry": round(sorted(atm_ivs)[len(atm_ivs) // 2], 4) if atm_ivs else None,
            "atm_call_delta": call.delta if call else None,
            "atm_put_delta": put.delta if put else None,
            "avg_bid_ask_spread_pct": round(sum(spreads) / len(spreads), 4) if spreads else None,
            "n_tradable_contracts": len(chain),
            "avg_daily_volume": round(sum(volumes) / len(volumes), 2) if volumes else None,
            "avg_last_trade_size": round(sum(trade_sizes) / len(trade_sizes), 2) if trade_sizes else None,
            "avg_quote_size": round(sum(quote_sizes) / len(quote_sizes), 2) if quote_sizes else None,
        }
    )

    # 25-delta skew inputs (nearest available per side)
    def _near25(contracts):
        with_delta = [c for c in contracts if c.delta and c.iv]
        if not with_delta:
            return None
        return min(with_delta, key=lambda c: abs(abs(c.delta) - 0.25))

    p25 = _near25([c for c in chain if c.opt_type == "put"])
    c25 = _near25([c for c in chain if c.opt_type == "call"])
    summary["put_25delta"] = p25.as_dict() if p25 else None
    summary["call_25delta"] = c25.as_dict() if c25 else None
    if p25 and c25 and p25.iv and c25.iv:
        summary["skew_put_call_25delta"] = round(p25.iv - c25.iv, 4)
    return summary


def candidate_strikes(chain: list[OptionContract], spot: float, expiry: str, n_each_side: int = 4) -> dict:
    """Liquid candidates around spot so the LLM picks REAL contract symbols."""
    out = {"calls": [], "puts": []}
    for opt_type in ("call", "put"):
        pool = [
            c
            for c in chain
            if c.expiry == expiry and c.opt_type == opt_type and c.iv and c.delta
        ]
        pool.sort(key=lambda c: abs(c.strike - spot))
        picked = sorted(pool[: n_each_side * 2 + 1], key=lambda c: c.strike)[: n_each_side * 2]
        out[f"{opt_type}s"] = [c.as_dict() for c in picked[: 2 * n_each_side]]
    return out


def build_candidate_whitelist(chain: list[OptionContract], quant_signal: dict,
                              spot: float, max_candidates: int = 8) -> list[dict]:
    """Return only contracts approved by the deterministic Quant gate."""
    import config

    direction = str(quant_signal.get("direction", "WAIT")).upper()
    if not quant_signal.get("actionable") or direction not in {"BULLISH", "BEARISH"}:
        return []
    probability = float(
        quant_signal.get("probability", quant_signal.get("probability_lower_bound") or 0)
    )
    lower_bound = float(quant_signal.get("probability_lower_bound") or probability)
    probability_floor = float(
        quant_signal.get("probability_floor", getattr(config, "MOMENTUM_MIN_PROBABILITY_LB", 0.55))
    )
    if probability < probability_floor and not quant_signal.get("historical_confidence_advisory"):
        return []
    expected_move = abs(float(quant_signal.get("expected_move_abs") or 0))
    if expected_move <= 0 or spot <= 0:
        return []

    opt_type = "call" if direction == "BULLISH" else "put"
    strategy_type = "LONG_CALL" if opt_type == "call" else "LONG_PUT"
    candidates = []
    for contract in chain:
        delta = abs(float(contract.delta or 0)) if contract.delta is not None else 0.0
        if contract.opt_type != opt_type:
            continue
        if not (
            getattr(config, "MOMENTUM_MIN_DTE", 7)
            <= contract.dte
            <= getattr(config, "MOMENTUM_MAX_DTE", 21)
        ):
            continue
        if not (
            getattr(config, "MOMENTUM_MIN_DELTA", 0.45)
            <= delta
            <= getattr(config, "MOMENTUM_MAX_DELTA", 0.70)
        ):
            continue
        if contract.bid <= 0 or contract.ask <= 0 or contract.ask < contract.bid:
            continue
        if contract.spread_pct > getattr(config, "MOMENTUM_MAX_SPREAD_PCT", 0.05):
            continue
        if contract.quote_timestamp:
            try:
                quote_time = datetime.fromisoformat(
                    str(contract.quote_timestamp).replace("Z", "+00:00")
                )
                if quote_time.tzinfo is None:
                    quote_time = quote_time.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - quote_time).total_seconds()
                if age < 0 or age > getattr(config, "MOMENTUM_MAX_QUOTE_AGE_SECONDS", 30):
                    continue
            except (TypeError, ValueError):
                continue
        projected_bid = contract.bid + delta * expected_move
        success_pnl = projected_bid - contract.ask
        expected_value = probability * success_pnl + (1 - probability) * (-contract.ask)
        if (
            quant_signal.get("require_positive_ev", True)
            and expected_value <= getattr(config, "MOMENTUM_MIN_EXPECTED_VALUE", 0.0)
        ):
            continue
        item = contract.as_dict()
        item.update({
            "candidate_id": contract.symbol,
            "direction": direction,
            "strategy_type": strategy_type,
            "probability": probability,
            "probability_lower_bound": lower_bound,
            "probability_floor": probability_floor,
            "probability_basis": quant_signal.get("probability_basis", "lower_bound"),
            "expected_value_after_costs": round(expected_value, 4),
            "projected_bid_at_target": round(projected_bid, 4),
            "quote_timestamp": getattr(contract, "quote_timestamp", None),
            "live_quote_activity": {
                "bid_size": contract.bid_size,
                "ask_size": contract.ask_size,
                "dominant_side": (
                    "ASK_HEAVY" if (contract.ask_size or 0) > (contract.bid_size or 0)
                    else "BID_HEAVY" if (contract.bid_size or 0) > (contract.ask_size or 0)
                    else "BALANCED"
                ) if contract.bid_size is not None or contract.ask_size is not None else "UNKNOWN",
            },
        })
        candidates.append(item)
    candidates.sort(key=lambda item: (-item["expected_value_after_costs"], item["spread_pct"], item["dte"]))
    return candidates[:max_candidates]


def build_shadow_candidate_list(
    chain: list[OptionContract], direction: str, max_candidates: int = 3
) -> list[dict]:
    """Return liquid directional contracts for observation only.

    Shadow candidates never satisfy or bypass the executable probability gate.
    """
    import config

    direction = str(direction).upper()
    opt_type = "call" if direction == "BULLISH" else "put" if direction == "BEARISH" else None
    if opt_type is None:
        return []
    candidates = []
    for contract in chain:
        delta = abs(float(contract.delta or 0)) if contract.delta is not None else 0.0
        if contract.opt_type != opt_type:
            continue
        if not config.SHADOW_MIN_DELTA <= delta <= config.SHADOW_MAX_DELTA:
            continue
        if not config.MOMENTUM_MIN_DTE <= contract.dte <= config.MOMENTUM_MAX_DTE:
            continue
        if contract.bid <= 0 or contract.ask <= 0 or contract.ask < contract.bid:
            continue
        if contract.spread_pct > config.MOMENTUM_MAX_SPREAD_PCT:
            continue
        item = contract.as_dict()
        item.update({
            "candidate_id": contract.symbol,
            "direction": direction,
            "strategy_type": "LONG_CALL" if direction == "BULLISH" else "LONG_PUT",
            "shadow": True,
        })
        candidates.append(item)
    candidates.sort(key=lambda item: (abs(abs(item["delta"]) - 0.55), item["spread_pct"], item["dte"]))
    return candidates[:max_candidates]
