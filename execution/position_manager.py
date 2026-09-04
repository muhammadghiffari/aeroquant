"""Position Manager: autonomous exits, run before any new entry.

Rules (deterministic, never LLM-overridden):
- anti-assignment: hard-close when expiry DTE <= PRE_EXPIRY_CLOSE_DAYS
- current entries: long-option TP/SL anchored to actual broker fill
- legacy multi-leg positions: retained only for reconciliation and safe closure
"""
import logging
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import config
from agents.news_earnings_agent import NewsEarningsAgent
from data_engine import alpaca_client
from data_engine.news_data import get_recent_news
from data_engine.option_data import fetch_chain, parse_occ
from data_engine.stock_data import get_intraday_bars
from execution import executor, ledger, operational_store
from execution.exit_policy import exit_decision
from quant_engine.momentum import build_momentum_signal

log = logging.getLogger(__name__)


def _chain_map(underlyings: set[str]) -> dict:
    # WIDE bounds: exits must be quotable even for legacy positions outside the
    # new-entry DTE window (otherwise they can never be closed)
    by_symbol: dict = {}
    for u in underlyings:
        try:
            for c in fetch_chain(u, min_dte=0, max_dte=180, max_spread_pct=1.0):
                by_symbol[c.symbol] = c
        except Exception as exc:  # noqa: BLE001
            log.warning("position_manager: chain fetch failed for %s: %s", u, exc)
    return by_symbol


def _unrealized_pl(position: dict, chain_by_symbol: dict) -> tuple[float | None, float]:
    """Return (pl_usd_total or None, close_cost_per_unit_signed)."""
    qty = int(position["qty"])
    close_cost_unit = 0.0
    ok = True
    for leg in position["legs"]:
        c = chain_by_symbol.get(leg["symbol"])
        if c is None:
            return None, 0.0
        action = "BUY" if leg["action"] == "SELL" else "SELL"
        executable = float(getattr(c, "ask", c.mid) or c.mid) if action == "BUY" else float(getattr(c, "bid", c.mid) or c.mid)
        close_cost_unit += executable * (1 if action == "BUY" else -1)
        if executable <= 0:
            ok = False
    if not ok or close_cost_unit == 0.0 and not position["legs"]:
        return None, close_cost_unit
    pl = (position["net_credit_or_debit_per_unit"] - close_cost_unit) * 100 * qty
    return round(pl, 2), round(close_cost_unit, 4)


_TERMINAL_BAD = {"canceled", "expired", "rejected"}


def _status_name(value) -> str:
    return str(value).split(".")[-1].lower()


def _strategy_type(legs: list[dict]) -> str | None:
    if len(legs) == 1:
        leg = legs[0]
        if leg["action"] == "BUY" and leg["opt_type"] == "call":
            return "LONG_CALL"
        if leg["action"] == "BUY" and leg["opt_type"] == "put":
            return "LONG_PUT"
        return None
    if len(legs) == 2:
        types = {leg["opt_type"] for leg in legs}
        sells = [leg for leg in legs if leg["action"] == "SELL"]
        buys = [leg for leg in legs if leg["action"] == "BUY"]
        if types == {"put"} and len(sells) == len(buys) == 1:
            return "BULL_PUT_SPREAD" if sells[0]["strike"] > buys[0]["strike"] else "DEBIT_SPREAD"
        if types == {"call"} and len(sells) == len(buys) == 1:
            return "BEAR_CALL_SPREAD" if sells[0]["strike"] < buys[0]["strike"] else "DEBIT_SPREAD"
    if len(legs) == 4 and {leg["opt_type"] for leg in legs} == {"put", "call"}:
        return "IRON_CONDOR"
    return None


def _set_fill_exit_levels(position: dict, entry_price: float) -> None:
    """Anchor deterministic TP/SL to the broker's actual average fill."""
    if position.get("strategy_type") not in {"LONG_CALL", "LONG_PUT"} or len(position.get("legs", [])) != 1:
        return
    entry = abs(float(entry_price or 0))
    if entry <= 0:
        return
    position.update({
        "entry_price": entry,
        "take_profit_price": round(entry * (1 + config.LONG_OPTION_TAKE_PROFIT_PCT), 2),
        "stop_loss_price": round(entry * (1 - config.LONG_OPTION_STOP_LOSS_PCT), 2),
    })


def _refresh_indicator_history(position: dict) -> None:
    if not position.get("monitor_context_enabled"):
        return
    try:
        bars = get_intraday_bars(
            position["underlying"],
            minutes=getattr(config, "MONITOR_LOOKBACK_MIN", 240),
        )
        signal = build_momentum_signal(
            bars,
            min_samples=getattr(config, "MOMENTUM_MIN_SAMPLES", 30),
            horizon=getattr(config, "MOMENTUM_HORIZON", 1),
        )
        features = signal.get("features")
        if not features:
            return
        history = list(position.get("indicator_history") or [])
        if not history or history[-1] != features:
            history.append(features)
        position["indicator_history"] = history[-getattr(config, "REVERSAL_HISTORY_MAX", 4):]
    except Exception as exc:  # noqa: BLE001
        log.warning("indicator refresh failed for %s: %s", position.get("id"), exc)


def _refresh_news_risk(position: dict) -> None:
    if not position.get("monitor_context_enabled"):
        return
    try:
        now = datetime.now(timezone.utc)
        checked_at = position.get("news_checked_at")
        if checked_at:
            previous = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            age = (now - previous).total_seconds()
            if 0 <= age < config.NEWS_REFRESH_INTERVAL_SECONDS:
                return
        items = get_recent_news(position["underlying"], limit=8)
        headlines = [str(item.get("headline", "")).strip() for item in items if item.get("headline")]
        checked_at = now.isoformat()
        position["news_checked_at"] = checked_at
        if not headlines:
            position["news_risk"] = {
                "event_risk": "LOW",
                "sentiment": "NEUTRAL",
                "confidence": 0.0,
                "headlines": [],
                "source": "alpaca_py_sdk",
                "checked_at": checked_at,
            }
            return
        report = NewsEarningsAgent().run({
            "symbol": position["underlying"],
            "headlines": headlines,
            "earnings": position.get("earnings", {}),
        })
        if str(report.get("event_risk", "")).upper() not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            return
        report["headlines"] = headlines
        report["source"] = "alpaca_py_sdk"
        report["checked_at"] = checked_at
        position["news_risk"] = report
    except Exception as exc:  # noqa: BLE001
        log.warning("news refresh failed for %s: %s", position.get("id"), exc)


def reconcile_untracked_filled_orders(data: dict) -> list[dict]:
    """Import filled agent orders after a crash before the ledger was saved."""
    known_orders = {str(p.get("client_order_id")) for p in data["positions"]}
    try:
        client = alpaca_client.trading_client()
        broker_positions = {
            str(pos.symbol): int(float(pos.qty))
            for pos in alpaca_client.safe("get_all_positions", client.get_all_positions)
        }
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = alpaca_client.safe(
            "get_orders",
            client.get_orders,
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reconcile_untracked_filled_orders failed: %s", exc)
        return []

    events: list[dict] = []
    for order in orders:
        client_order_id = str(getattr(order, "client_order_id", "") or "")
        if not client_order_id.startswith("agent-") or client_order_id in known_orders:
            continue
        if _status_name(getattr(order, "status", "")) != "filled":
            continue
        raw_legs = list(getattr(order, "legs", None) or [])
        if not raw_legs:
            symbol = str(getattr(order, "symbol", "") or "")
            side = getattr(order, "side", None)
            position_intent = getattr(order, "position_intent", None)
            if symbol and side and position_intent:
                raw_legs = [SimpleNamespace(
                    symbol=symbol,
                    side=side,
                    ratio_qty=1,
                    position_intent=position_intent,
                )]
        if not raw_legs or not all(
            _status_name(getattr(leg, "position_intent", "")).endswith("to_open")
            for leg in raw_legs
        ):
            continue

        legs: list[dict] = []
        valid = True
        for raw_leg in raw_legs:
            symbol = str(raw_leg.symbol)
            parsed = parse_occ(symbol)
            if not parsed:
                valid = False
                break
            expiry, opt_type, strike = parsed
            action = _status_name(raw_leg.side).upper()
            if action not in {"BUY", "SELL"}:
                valid = False
                break
            ratio_qty = int(float(getattr(raw_leg, "ratio_qty", 1) or 1))
            expected = ratio_qty if action == "BUY" else -ratio_qty
            if broker_positions.get(symbol, 0) != expected * int(float(order.filled_qty or order.qty or 0)):
                valid = False
                break
            legs.append({
                "action": action,
                "symbol": symbol,
                "strike": strike,
                "expiry": expiry,
                "opt_type": opt_type,
                "qty": ratio_qty,
            })
        if not valid:
            continue

        strategy_type = _strategy_type(legs)
        if strategy_type is None:
            continue
        filled_avg = float(order.filled_avg_price or 0)
        net = round(-filled_avg, 2)
        if strategy_type in {"BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR"}:
            width = max(
                abs(s["strike"] - b["strike"])
                for s in legs if s["action"] == "SELL"
                for b in legs if b["action"] == "BUY"
                if s["opt_type"] == b["opt_type"]
            )
            max_loss = max(width - net, 0.01) * 100
        else:
            max_loss = max(abs(net), 0.01) * 100

        position = {
            "id": str(uuid.uuid4())[:8],
            "underlying": re.match(r"^([A-Z]+)\d{6}[CP]\d{8}$", legs[0]["symbol"]).group(1),
            "strategy_type": strategy_type,
            "qty": int(float(order.filled_qty or order.qty or 1)),
            "legs": legs,
            "net_credit_or_debit_per_unit": net,
            "max_loss_usd": round(max_loss, 2),
            "client_order_id": client_order_id,
            "order_id": str(order.id),
            "status": "OPEN",
            "opened_at": str(getattr(order, "filled_at", None) or getattr(order, "submitted_at", None) or datetime.now(timezone.utc).isoformat()),
            "recovered": True,
        }
        _set_fill_exit_levels(position, filled_avg)
        data["positions"].append(position)
        known_orders.add(client_order_id)
        events.append({
            "id": str(order.id),
            "reason": "imported_filled_order",
            "underlying": position["underlying"],
        })
    return events


def reconcile_unfilled(data: dict) -> list[dict]:
    """Heal stale ledger positions whose broker order died unfilled.

    Restores the lost 'order_never_filled_reconciled' behavior: an OPEN ledger
    entry whose order was canceled/expired/rejected at the broker is marked
    CLOSED with zero realized P/L so it stops blocking exposure checks.
    """
    out: list[dict] = []
    for pos in [p for p in data["positions"] if p["status"] == "OPEN"]:
        oid = pos.get("order_id")
        if not oid:
            continue
        try:
            order = alpaca_client.safe(
                "get_order_by_id", alpaca_client.trading_client().get_order_by_id, oid
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("reconcile: cannot fetch order %s: %s", oid, exc)
            continue
        status = str(order.status).split(".")[-1].lower()
        if status in _TERMINAL_BAD:
            ledger.mark_closed(data, pos["id"], "order_never_filled_reconciled", 0.0)
            out.append(
                {
                    "id": pos["id"],
                    "underlying": pos.get("underlying"),
                    "reason": "order_never_filled_reconciled",
                    "broker_status": status,
                }
            )
    return out


def _external_close_realized_pl(position: dict) -> float | None:
    """Recover broker fill P/L for a manually closed single long option."""
    legs = position.get("legs") or []
    if len(legs) != 1 or legs[0].get("action") != "BUY":
        return None
    try:
        entry = _order_by_id(position["order_id"])
        entry_price = float(getattr(entry, "filled_avg_price", 0) or 0)
        if entry_price <= 0:
            return None
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = alpaca_client.safe(
            "get_orders", alpaca_client.trading_client().get_orders,
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500),
        )
        symbol = legs[0]["symbol"]
        closes = [
            order for order in orders
            if str(getattr(order, "symbol", "")) == symbol
            and _status_name(getattr(order, "side", "")) == "sell"
            and _status_name(getattr(order, "status", "")) == "filled"
            and float(getattr(order, "filled_qty", 0) or 0) >= int(position.get("qty", 1))
        ]
        if not closes:
            return None
        close = max(closes, key=lambda order: str(getattr(order, "submitted_at", "")))
        close_price = float(getattr(close, "filled_avg_price", 0) or 0)
        if close_price <= 0:
            return None
        return round((close_price - entry_price) * 100 * int(position.get("qty", 1)), 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("external close P/L unavailable for %s: %s", position.get("id"), exc)
        return None


def reconcile_with_broker(data: dict) -> list[dict]:
    """Self-healing: make the ledger match ACTUAL broker positions.

    The broker is the source of truth. If legs vanished externally (manual
    close in the Alpaca UI, partial fills, assignment), heal the ledger:
    - all legs gone            -> mark CLOSED (reconciled, P/L unknown = 0)
    - some legs remain         -> close the orphans at market, then mark CLOSED
    """
    open_positions = [
        p for p in data["positions"]
        if p["status"] in {"OPEN", "RECOVERY_REQUIRED"}
    ]
    if not open_positions:
        return []
    try:
        broker = {
            pos.symbol: pos
            for pos in alpaca_client.safe(
                "get_all_positions", alpaca_client.trading_client().get_all_positions
            )
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("reconcile_with_broker: cannot list positions: %s", exc)
        return []

    events: list[dict] = []
    for pos in open_positions:
        states = []
        for leg in pos["legs"]:
            b = broker.get(leg["symbol"])
            expected = int(leg.get("qty", 1)) * (1 if leg["action"] == "BUY" else -1)
            actual = int(float(b.qty)) if b else 0
            states.append(actual == expected)

        intact = sum(1 for s in states if s)
        if intact == len(states):
            if pos.get("status") == "RECOVERY_REQUIRED":
                pos["status"] = "OPEN"
                pos.pop("recovery_reason", None)
                pos.pop("recovery_at", None)
                events.append({
                    "id": pos["id"], "underlying": pos.get("underlying"),
                    "reason": "recovery_resolved",
                })
            if (
                pos.get("strategy_type") in {"LONG_CALL", "LONG_PUT"}
                and len(pos["legs"]) == 1
                and pos["legs"][0]["action"] == "BUY"
            ):
                avg_entry_price = float(getattr(broker[pos["legs"][0]["symbol"]], "avg_entry_price", 0) or 0)
                if avg_entry_price > 0:
                    pos["net_credit_or_debit_per_unit"] = -avg_entry_price
                    _set_fill_exit_levels(pos, avg_entry_price)
            continue  # fully intact -- nothing to do

        if intact == 0:
            realized = _external_close_realized_pl(pos)
            ledger.mark_closed(
                data, pos["id"], "closed_externally_reconciled",
                realized if realized is not None else 0.0,
            )
            events.append({
                "id": pos["id"], "underlying": pos.get("underlying"),
                "reason": "closed_externally_reconciled",
            })
            continue

        # A partial broker position is unsafe to repair from a local guess.
        # Keep it visible and block entries until a monitored recovery resolves it.
        ledger.mark_recovery_required(data, pos["id"], "partial_broker_position")
        events.append({
            "id": pos["id"], "underlying": pos.get("underlying"),
            "reason": "partial_broker_position",
        })
    return events


def _order_by_id(order_id: str):
    return alpaca_client.safe(
        "get_order_by_id", alpaca_client.trading_client().get_order_by_id, order_id
    )


def reconcile_order_intents() -> list[dict]:
    """Refresh unresolved intent rows so crashed submissions can converge."""
    events: list[dict] = []
    for intent in operational_store.unresolved_intents():
        try:
            client = alpaca_client.trading_client()
            if intent.get("broker_order_id"):
                order = _order_by_id(intent["broker_order_id"])
            else:
                order = alpaca_client.safe(
                    "get_order_by_client_id", client.get_order_by_client_id,
                    intent["client_order_id"],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("intent %s unresolved: %s", intent.get("intent_id"), exc)
            continue
        status = _status_name(getattr(order, "status", ""))
        if status == "filled":
            operational_store.update_order_status(intent["intent_id"], "FILLED", status)
        elif status in _TERMINAL_BAD:
            operational_store.update_order_status(intent["intent_id"], status.upper(), status)
        else:
            operational_store.update_order_status(intent["intent_id"], "SUBMITTED", status)
        events.append({"intent_id": intent["intent_id"], "reason": f"intent_{status}"})
    return events


def reconcile_pending_entries(data: dict) -> list[dict]:
    """Resolve locally tracked entry intents against their broker order."""
    events: list[dict] = []
    for pos in [p for p in data["positions"] if p.get("status") == "PENDING_ENTRY"]:
        try:
            order = _order_by_id(pos["order_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("pending entry: cannot fetch order %s: %s", pos.get("order_id"), exc)
            continue
        status = _status_name(getattr(order, "status", ""))
        if status == "filled":
            fill = float(getattr(order, "filled_avg_price", 0) or 0)
            if fill > 0 and len(pos.get("legs", [])) == 1 and pos["legs"][0]["action"] == "BUY":
                pos["net_credit_or_debit_per_unit"] = -fill
                pos["max_loss_usd"] = round(fill * 100 * int(pos.get("qty", 1)), 2)
                _set_fill_exit_levels(pos, fill)
            elif fill:
                pos["net_credit_or_debit_per_unit"] = -fill
            pos["status"] = "OPEN"
            pos["entry_status"] = "filled"
            pos["filled_at"] = str(getattr(order, "filled_at", None) or datetime.now(timezone.utc).isoformat())
            events.append({"id": pos["id"], "reason": "entry_filled"})
        elif status in _TERMINAL_BAD:
            ledger.mark_closed(data, pos["id"], "entry_rejected", 0.0)
            events.append({"id": pos["id"], "reason": "entry_rejected", "broker_status": status})
    return events


def reconcile_closing_positions(data: dict) -> list[dict]:
    """Resolve close orders without assuming submission equals execution."""
    events: list[dict] = []
    for pos in [p for p in data["positions"] if p.get("status") == "CLOSING"]:
        try:
            order = _order_by_id(pos["closing_order_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("closing position: cannot fetch order %s: %s", pos.get("closing_order_id"), exc)
            continue
        status = _status_name(getattr(order, "status", ""))
        if status == "filled":
            fill = abs(float(getattr(order, "filled_avg_price", 0) or 0))
            entry = float(pos.get("net_credit_or_debit_per_unit", 0) or 0)
            if len(pos.get("legs", [])) == 1 and pos["legs"][0]["action"] == "BUY":
                realized = (entry + fill) * 100 * int(pos.get("qty", 1))
            else:
                realized = (entry - fill) * 100 * int(pos.get("qty", 1))
            ledger.mark_closed(data, pos["id"], pos.get("closing_reason", "closed"), realized)
            events.append({
                "id": pos["id"],
                "reason": "close_filled",
                "realized_pl": round(realized, 2),
                "order_id": pos["closing_order_id"],
            })
        elif status in _TERMINAL_BAD:
            pos["status"] = "OPEN"
            pos.pop("closing_order_id", None)
            pos.pop("closing_reason", None)
            events.append({"id": pos["id"], "reason": "close_rejected", "broker_status": status})
        else:
            events.append({"id": pos["id"], "reason": "close_pending", "broker_status": status})
    return events


def manage_positions(data: dict) -> list[dict]:
    """Evaluate all OPEN positions; execute exits where rules trigger.

    Mutates `data` (ledger). Returns list of exit records.
    """
    events = reconcile_order_intents()
    events += reconcile_closing_positions(data)
    events += reconcile_pending_entries(data)
    events += reconcile_with_broker(data)
    events += reconcile_untracked_filled_orders(data)
    events += reconcile_unfilled(data)
    open_positions = [p for p in data["positions"] if p["status"] == "OPEN"]
    if not open_positions:
        return events

    today = config.market_date()
    for pos in open_positions:
        _refresh_indicator_history(pos)
        _refresh_news_risk(pos)
    chain_by_symbol = _chain_map({p["underlying"] for p in open_positions})
    exits: list[dict] = list(events)

    for pos in open_positions:
        dte = None
        expiries = {leg["expiry"] for leg in pos["legs"]}
        if len(expiries) == 1:
            from datetime import date as _date

            e = min(
                (_date.fromisoformat(x) for x in expiries), default=None
            )
            if e:
                dte = (e - today).days

        policy = exit_decision(
            pos,
            chain_by_symbol,
            today,
            indicator_history=pos.get("indicator_history", []),
            news_risk=pos.get("news_risk"),
        )
        reason = policy["reason"] if policy else None
        if reason is None and dte is not None and dte <= config.PRE_EXPIRY_CLOSE_DAYS:
            reason = f"anti_assignment_dte_{dte}"

        if reason is None and pos.get("strategy_type") not in {"LONG_CALL", "LONG_PUT"}:
            pl, _close_cost = _unrealized_pl(pos, chain_by_symbol)
            if pl is not None:
                credit_based = pos["net_credit_or_debit_per_unit"] > 0
                base = abs(pos["net_credit_or_debit_per_unit"]) * 100 * int(pos["qty"])
                if credit_based:
                    if pl >= 0.5 * base:
                        reason = f"take_profit_pl_{pl}"
                    elif pl <= -2.0 * base:
                        reason = f"stop_loss_pl_{pl}"
                else:
                    if pl >= 1.0 * base:
                        reason = f"take_profit_pl_{pl}"
                    elif pl <= -0.5 * base:
                        reason = f"stop_loss_pl_{pl}"

        if reason is None:
            continue

        result = executor.close_position(pos, chain_by_symbol)
        if result is None:
            log.warning("exit failed for %s (%s); will retry next cycle", pos["id"], reason)
            continue
        record = {
            "id": str(uuid.uuid4())[:8],
            "position_id": pos["id"],
            "reason": reason,
            **result,
        }
        exits.append(record)
        pos["status"] = "CLOSING"
        pos["closing_order_id"] = result["order_id"]
        pos["closing_reason"] = reason.split("_pl_")[0]

    return exits


def exposure_pct(data: dict) -> float:
    """Sum of remaining max-loss $ of OPEN positions as % of equity."""
    total = sum(
        p.get("max_loss_usd", 0) * int(p.get("qty", 1))
        for p in data["positions"]
        if p["status"] in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}
    )
    acct = alpaca_client.safe("get_account", alpaca_client.trading_client().get_account)
    equity = float(acct.equity or 0)
    return (total / equity * 100) if equity else 0.0


def symbol_exposure_pct(data: dict, underlying: str, equity: float) -> float:
    """Return max-loss exposure for one underlying as a percent of equity."""
    if equity <= 0:
        return float("inf")
    total = sum(
        p.get("max_loss_usd", 0) * int(p.get("qty", 1))
        for p in data["positions"]
        if p.get("status") in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}
        and str(p.get("underlying", "")).upper() == underlying.upper()
    )
    return total / equity * 100


def open_positions_count(data: dict) -> int:
    return sum(
        1 for p in data["positions"]
        if p["status"] in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}
    )
