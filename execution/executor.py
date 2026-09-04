"""Execution Agent: submit approved proposals to Alpaca (PAPER ONLY).

Verified API facts (alpaca-py 0.44 / docs):
- Multi-leg = LimitOrderRequest/MarketOrderRequest with order_class=MLEG and
  legs=[OptionLegRequest(symbol, ratio_qty, side, position_intent)]. Max 4 legs.
- limit_price semantics for MLEG: positive = max net DEBIT; negative = min net
  CREDIT to receive. We always send padded limits, never market orders.
- Individual legs of an MLEG cannot be cancelled/replaced -- whole order only.
"""
import hashlib
import json
import logging
import time

import config
from alpaca.trading.enums import OrderClass, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

from data_engine import alpaca_client

log = logging.getLogger(__name__)

_INTENT = {
    ("BUY", "open"): "buy_to_open",
    ("SELL", "open"): "sell_to_open",
    ("BUY", "close"): "buy_to_close",
    ("SELL", "close"): "sell_to_close",
}


def _client_order_id(proposal: dict) -> str:
    h = hashlib.md5(
        json.dumps([l["symbol"] + l["action"] for l in proposal["legs"]], sort_keys=True).encode()
    ).hexdigest()[:8]
    return f"agent-{proposal['symbol'].lower()}-{h}-{int(time.time())}"


def build_client_order_id(proposal: dict, legs: list[dict]) -> str:
    """Create the idempotency key before persisting an order intent."""
    return _client_order_id({**proposal, "legs": legs})


def _submit_order(request, client_order_id: str):
    """Submit once, then recover an accepted order if the response is ambiguous."""
    client = alpaca_client.trading_client()
    try:
        return alpaca_client.safe("submit_order", client.submit_order, order_data=request)
    except Exception as original:
        try:
            existing = client.get_order_by_client_id(client_order_id)
        except Exception:
            raise original
        log.warning("submit response failed but order %s exists; using broker result", client_order_id)
        return existing


def _limit_price(strategy_type: str, legs: list[dict], qty: int) -> float:
    """Net price per unit; pad toward adverse side by slippage tolerance."""
    sells = [l for l in legs if l["action"] == "SELL"]
    buys = [l for l in legs if l["action"] == "BUY"]
    executable_quotes = all(
        (leg.get("_bid") is not None if leg["action"] == "SELL" else leg.get("_ask") is not None)
        for leg in legs
    )
    credit = sum(l["_bid"] if l.get("_bid") is not None else l["_mid"] for l in sells)
    debit = sum(l["_ask"] if l.get("_ask") is not None else l["_mid"] for l in buys)
    net = credit - debit  # >0 credit
    if executable_quotes:
        return round(-net if net >= 0 else abs(net), 2)
    pad = abs(net) * config.LIMIT_PRICE_SLIPPAGE_PCT
    if net > -1e-9:  # credit or even: receive at least mid-pad -> negative limit
        return round(-(max(net - pad, 0.0)), 2)
    # debit: pay at most mid+pad -> positive limit
    return round((abs(net) + pad), 2)


def submit_strategy(proposal: dict, risk_decision: dict, client_order_id: str | None = None) -> dict:
    """Submit one approved strategy. Returns execution record dict."""
    assert config.PAPER_TRADE, "SAFETY: refusing to trade outside paper mode"

    legs_meta = risk_decision["recomputed"]["resolved_legs"]
    qty = int(risk_decision.get("adjusted_qty", 1))
    stype = proposal["strategy_type"]
    if (
        stype not in {"LONG_CALL", "LONG_PUT"}
        or len(legs_meta) != 1
        or legs_meta[0].get("action") != "BUY"
        or qty != 1
    ):
        raise ValueError("executor accepts one BUY leg for a single long option only")

    order_legs_for_pricing = [
        {"action": m["action"], "_mid": m["mid"], "_bid": m.get("bid"), "_ask": m.get("ask")}
        for m in legs_meta
    ]
    limit_price = _limit_price(stype, order_legs_for_pricing, qty)
    client_order_id = client_order_id or build_client_order_id(proposal, legs_meta)
    if len(legs_meta) == 1:
        leg = legs_meta[0]
        req = LimitOrderRequest(
            symbol=leg["symbol"],
            qty=qty,
            side="buy" if leg["action"] == "BUY" else "sell",
            position_intent=_INTENT[(leg["action"], "open")],
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            client_order_id=client_order_id,
        )
    else:
        leg_reqs = [
            OptionLegRequest(
                symbol=m["symbol"],
                ratio_qty=1,
                side="buy" if m["action"] == "BUY" else "sell",
                position_intent=_INTENT[(m["action"], "open")],
            )
            for m in legs_meta
        ]
        req = LimitOrderRequest(
            qty=qty,
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            legs=leg_reqs,
            client_order_id=client_order_id,
        )

    log.info("submitting %s x%d @ net %s: %s", stype, qty, limit_price,
             [m["symbol"] for m in legs_meta])
    order = _submit_order(req, client_order_id)

    return {
        "strategy_type": stype,
        "qty": qty,
        "limit_net": limit_price,
        "order_id": str(order.id),
        "client_order_id": order.client_order_id,
        "status": str(order.status),
        "legs": [
            {k: v for k, v in m.items()} for m in legs_meta
        ],
        "submitted_at": time.time(),
    }


def close_single_leg(leg_symbol: str, held_side: str, qty: int) -> dict | None:
    """Close ONE orphaned option leg (external intervention / partial fill).

    held_side: 'long' -> we hold it, SELL to close; 'short' -> BUY to close.
    Uses a wide limit pad for near-certain fill.
    """
    assert config.PAPER_TRADE, "SAFETY: refusing to trade outside paper mode"

    side = "sell" if held_side == "long" else "buy"
    try:
        # price it from the live chain when possible
        try:
            from data_engine.option_data import fetch_chain

            sym_root = leg_symbol.split("0", 2)[0]
            chain = fetch_chain(sym_root, min_dte=0, max_dte=180, max_spread_pct=1.0)
            c = next((x for x in chain if x.symbol == leg_symbol), None)
            if c:
                pad = max(c.mid * 0.30, 0.02)
                px = c.mid + pad if side == "buy" else max(c.mid - pad, 0.01)
                limit_price = round(px, 2)
            else:
                limit_price = 0.01 if side == "sell" else 25.0
        except Exception:  # noqa: BLE001
            limit_price = 0.01 if side == "sell" else 25.0

        req = LimitOrderRequest(
            symbol=leg_symbol,
            qty=qty,
            side=side,
            position_intent=_INTENT[("SELL" if held_side == "long" else "BUY", "close")],
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            client_order_id=f"heal-{leg_symbol[:16]}-{int(time.time())}",
        )

        order = _submit_order(req, req.client_order_id)
        log.info("heal: closing orphan leg %s (%s) x%d", leg_symbol, held_side, qty)
        return {"order_id": str(order.id), "status": str(order.status)}
    except Exception as exc:  # noqa: BLE001
        log.error("heal: cannot close leg %s: %s", leg_symbol, exc)
        return None


def close_position(position: dict, chain_by_symbol: dict) -> dict | None:
    """Submit a simple or MLEG closing order for a tracked ledger position."""
    assert config.PAPER_TRADE, "SAFETY: refusing to trade outside paper mode"

    qty = int(position["qty"])
    leg_reqs = []
    total_close_cost = 0.0
    for leg in position["legs"]:
        contract = chain_by_symbol.get(leg["symbol"])
        if contract is None:
            log.error("cannot close %s: no quote for %s", position["id"], leg["symbol"])
            return None
        action = "BUY" if leg["action"] == "SELL" else "SELL"
        leg_reqs.append(
            OptionLegRequest(
                symbol=leg["symbol"],
                ratio_qty=1,
                side=action.lower(),
                position_intent=_INTENT[(action, "close")],
            )
        )
        executable = (
            getattr(contract, "ask", contract.mid)
            if action == "BUY"
            else getattr(contract, "bid", contract.mid)
        )
        total_close_cost += float(executable or contract.mid) * (1 if action == "BUY" else -1)

    close_cost = total_close_cost  # positive = we pay (debit to close)
    entry_net = position["net_credit_or_debit_per_unit"]
    pad = abs(close_cost) * config.LIMIT_PRICE_SLIPPAGE_PCT
    if close_cost > 0:
        limit_price = round(close_cost + pad, 2)
    else:
        limit_price = round(min(close_cost + pad, 0.0), 2)

    client_order_id = f"close-{position['id'][:24]}"
    if len(leg_reqs) == 1:
        leg = position["legs"][0]
        contract = chain_by_symbol[leg["symbol"]]
        action = "BUY" if leg["action"] == "SELL" else "SELL"
        pad = max(contract.mid * config.LIMIT_PRICE_SLIPPAGE_PCT, 0.01)
        if action == "BUY":
            limit_price = round(contract.mid + pad, 2)
        else:
            executable_bid = float(getattr(contract, "bid", 0) or 0)
            limit_price = round(executable_bid if executable_bid > 0 else max(contract.mid - pad, 0.01), 2)
        req = LimitOrderRequest(
            symbol=leg["symbol"],
            qty=qty,
            side=action.lower(),
            position_intent=_INTENT[(action, "close")],
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            client_order_id=client_order_id,
        )
    else:
        req = LimitOrderRequest(
            qty=qty,
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            legs=leg_reqs,
            client_order_id=client_order_id,
        )
    log.info("closing %s (%s) x%d @ net %s", position["id"], position["strategy_type"],
             qty, limit_price)
    try:
        order = _submit_order(req, client_order_id)
    except Exception as exc:  # noqa: BLE001
        log.error("cannot submit close for %s: %s", position["id"], exc)
        return None
    realized = (entry_net - close_cost) * 100 * qty  # credit>0: profit when cost<credit
    return {
        "order_id": str(order.id),
        "client_order_id": order.client_order_id,
        "status": str(order.status),
        "close_cost_per_unit": round(close_cost, 2),
        "estimated_realized_pl": round(realized, 2),
    }
