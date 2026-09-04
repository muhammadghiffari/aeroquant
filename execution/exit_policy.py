"""Pure, conservative exit decisions for tracked long-option positions."""
from datetime import date, datetime, timezone

import config
from quant_engine.momentum import score_reversal


def _quote_is_usable(contract) -> bool:
    bid = float(getattr(contract, "bid", 0) or 0)
    ask = float(getattr(contract, "ask", 0) or 0)
    if bid <= 0 or ask < bid:
        return False
    spread_pct = getattr(contract, "spread_pct", None)
    if spread_pct is not None and float(spread_pct) > config.MOMENTUM_MAX_SPREAD_PCT:
        return False
    raw_timestamp = getattr(contract, "quote_timestamp", None)
    if not raw_timestamp:
        return True
    try:
        timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        return 0 <= age <= config.EXIT_QUOTE_MAX_AGE_SECONDS
    except (TypeError, ValueError):
        return False


def _verified_critical_news(news_risk) -> bool:
    """Require fresh, sourced evidence before a forced news close."""
    if not isinstance(news_risk, dict):
        return False
    if str(news_risk.get("event_risk", "")).upper() != "CRITICAL":
        return False
    headlines = news_risk.get("headlines")
    if not isinstance(headlines, list) or not any(str(item).strip() for item in headlines):
        return False
    if str(news_risk.get("source", "")).strip() not in {"mcp_server", "alpaca_py_sdk"}:
        return False
    try:
        confidence = float(news_risk.get("confidence"))
        checked_at = news_risk.get("checked_at") or news_risk.get("timestamp")
        timestamp = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    except (TypeError, ValueError):
        return False
    return (
        config.CRITICAL_NEWS_MIN_CONFIDENCE <= confidence <= 1.0
        and 0 <= age <= config.CRITICAL_NEWS_MAX_AGE_SECONDS
    )


def exit_decision(
    position: dict,
    chain_by_symbol: dict,
    today: date | None = None,
    indicator_history: list[dict] | None = None,
    news_risk: str | dict | None = None,
) -> dict | None:
    """Return a close reason and executable P/L estimate, or ``None`` to hold."""
    today = today or config.market_date()
    qty = int(position["qty"])
    legs = position["legs"]
    if not legs or position.get("strategy_type") not in {"LONG_CALL", "LONG_PUT"}:
        return None

    if today >= config.FINAL_CLOSE_DATE:
        return {"reason": "force_close_final_deadline"}

    expiries = {leg.get("expiry") for leg in legs}
    if len(expiries) == 1 and next(iter(expiries)):
        expiry = date.fromisoformat(next(iter(expiries)))
        if (expiry - today).days <= config.PRE_EXPIRY_CLOSE_DAYS:
            return {"reason": "force_close_pre_expiry"}

    critical_news = _verified_critical_news(news_risk)

    proceeds = 0.0
    quote_usable = True
    for leg in legs:
        contract = chain_by_symbol.get(leg["symbol"])
        if contract is None:
            return {"reason": "critical_news"} if critical_news else None
        quote_usable = quote_usable and _quote_is_usable(contract)
        # A long option exits by selling at bid, never an optimistic midpoint.
        proceeds += float(contract.bid) if leg["action"] == "BUY" else -float(contract.ask)

    entry_credit_or_debit = float(position["net_credit_or_debit_per_unit"])
    realized = round((entry_credit_or_debit + proceeds) * 100 * qty, 2)
    entry_cost = abs(entry_credit_or_debit) * 100 * qty
    if entry_cost <= 0:
        return {"reason": "critical_news"} if critical_news else None
    if position.get("take_profit_price") and proceeds >= float(position["take_profit_price"]):
        return {"reason": "take_profit", "estimated_realized_pl": realized}
    if position.get("stop_loss_price") and proceeds <= float(position["stop_loss_price"]):
        return {"reason": "stop_loss", "estimated_realized_pl": realized}
    if critical_news:
        return {"reason": "critical_news", "estimated_realized_pl": realized}
    pnl_pct = realized / entry_cost
    if pnl_pct >= config.LONG_OPTION_MIN_PROFIT_PCT:
        return {"reason": "take_profit_executable", "estimated_realized_pl": realized}
    if pnl_pct <= -config.LONG_OPTION_STOP_LOSS_PCT:
        return {"reason": "stop_loss_executable", "estimated_realized_pl": realized}
    reversal = score_reversal(indicator_history, position["strategy_type"])
    if quote_usable and reversal["confirmed"]:
        return {"reason": "confirmed_reversal", "reversal": reversal, "estimated_realized_pl": realized}
    return None
