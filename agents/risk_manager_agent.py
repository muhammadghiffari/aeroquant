"""Risk Manager Agent -- hard gate before execution.

Deterministic rules are recomputed from REAL market data (never trusting the
chief's claimed numbers). The LLM adds an advisory sanity review only when all
rules already pass; it can never override a rejection (spec section 11).
"""
import logging
import math

import config
from agents.base_agent import BaseAgent
from data_engine.option_data import resolve_leg

log = logging.getLogger(__name__)

_CREDIT_STRATS = {"BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR"}
_DEBIT_STRATS = {"DEBIT_SPREAD"}
_LONG_STRATS = {"LONG_CALL", "LONG_PUT"}
_ALLOWED_ENTRY_STRATS = _LONG_STRATS | {"WAIT"}


def validate_quant_entry(proposal: dict, quant_report: dict) -> tuple[bool, str]:
    """Validate the proposal against the current deterministic Quant gate."""
    if not quant_report.get("entry_actionable"):
        return False, "quant_entry_gate_not_actionable"
    confidence = quant_report.get("confidence") or {}
    if (
        confidence
        and confidence.get("state") not in {"ENTER_CONFIRMED", "GREEN", "GREEN_PROXY"}
        and not confidence.get("historical_confidence_advisory")
    ):
        return False, "confidence_gate_not_confirmed"
    direction = str(
        confidence.get("direction") if confidence else quant_report.get("direction", "WAIT")
    ).upper()
    expected_type = {"BULLISH": "LONG_CALL", "BEARISH": "LONG_PUT"}.get(direction)
    if proposal.get("strategy_type") != expected_type:
        return False, "quant_direction_mismatch"
    candidate_id = proposal.get("candidate_id")
    candidate = next(
        (item for item in quant_report.get("candidates", [])
         if item.get("candidate_id") == candidate_id),
        None,
    )
    if candidate is None:
        return False, "candidate_not_in_quant_whitelist"
    legs = proposal.get("legs") or []
    if len(legs) != 1 or legs[0].get("action") != "BUY" or legs[0].get("qty") != 1:
        return False, "single_leg_shape_invalid"
    if legs[0].get("symbol") != candidate.get("symbol"):
        return False, "candidate_symbol_mismatch"
    if candidate.get("strategy_type") != expected_type:
        return False, "candidate_strategy_mismatch"
    return True, ""


class RuleCheckResult:
    def __init__(self) -> None:
        self.checks: dict[str, bool] = {}
        self.notes: list[str] = []
        self.resolved_legs: list[dict] = []
        self.max_loss_unit = 0.0     # $ per strategy unit (1 contract set)
        max_profit_unit = 0.0        # noqa: F841 (kept for report clarity)
        self.max_profit_unit = 0.0
        self.net_price_unit = 0.0    # positive=credit received, negative=debit paid
        self.adjusted_qty = 0


def _unit_metrics(strategy_type: str, legs: list[dict]) -> tuple[float, float, float]:
    """Return (max_loss_$, max_profit_$, net_price_signed) per 1 contract set."""
    sells = [l for l in legs if l["action"] == "SELL"]
    buys = [l for l in legs if l["action"] == "BUY"]

    def executable(leg: dict) -> float:
        contract = leg["_contract"]
        if leg["action"] == "BUY":
            return float(getattr(contract, "ask", contract.mid) or contract.mid)
        return float(getattr(contract, "bid", contract.mid) or contract.mid)

    credit = sum(executable(l) for l in sells)
    debit = sum(executable(l) for l in buys)
    net = round(credit - debit, 2)  # >0 credit

    if strategy_type in _CREDIT_STRATS:
        widths = [
            abs(a["_contract"].strike - b["_contract"].strike)
            for i, a in enumerate(sells)
            for b in buys
            if a["_contract"].opt_type == b["_contract"].opt_type
        ]
        # condor: two wings (put wing + call wing); spreads: single wing
        wing = max(widths) if widths else 0.0
        max_loss = max(wing - net, 0.01)
        max_profit = net
    elif strategy_type in _DEBIT_STRATS:
        strikes = [l["_contract"].strike for l in legs]
        width = abs(strikes[0] - strikes[1]) if len(strikes) >= 2 else 0.0
        max_loss = max(debit, 0.01)
        max_profit = max(width - debit, 0.0)
    else:  # long single leg
        max_loss = max(debit, 0.01)
        max_profit = float("inf")
    return round(max_loss * 100, 2), round(max_profit * 100, 2), net


def _strikes_by_action(resolved: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {"BUY": [], "SELL": []}
    for e in resolved:
        out[e["action"]].append(float(e["_contract"].strike))
    return out


def _structure_consistent(stype: str, resolved: list[dict]) -> bool:
    """Validate leg DIRECTION, not just leg count.

    Catches inverted spreads the LLM sometimes builds (e.g. SELL 760P / BUY
    764P labeled BULL_PUT_SPREAD) before any money math is done.
    """
    s = _strikes_by_action(resolved)
    types = {e["_contract"].opt_type for e in resolved}

    if stype == "LONG_CALL":
        return types == {"call"} and len(resolved) == 1 and resolved[0]["action"] == "BUY"
    if stype == "LONG_PUT":
        return types == {"put"} and len(resolved) == 1 and resolved[0]["action"] == "BUY"
    if stype == "BULL_PUT_SPREAD":
        # sell the put strike NEARER spot (higher), buy the one further below
        return (
            types == {"put"} and len(resolved) == 2
            and bool(s["SELL"]) and bool(s["BUY"])
            and min(s["SELL"]) > max(s["BUY"])
        )
    if stype == "BEAR_CALL_SPREAD":
        # sell the call strike NEARER spot (lower), buy the one further above
        return (
            types == {"call"} and len(resolved) == 2
            and bool(s["SELL"]) and bool(s["BUY"])
            and max(s["SELL"]) < min(s["BUY"])
        )
    if stype == "DEBIT_SPREAD":
        if len(resolved) != 2 or not s["BUY"] or not s["SELL"]:
            return False
        if types == {"put"}:    # bullish put debit: BUY higher strike, SELL lower
            return min(s["BUY"]) > max(s["SELL"])
        if types == {"call"}:   # bullish call debit: BUY lower strike, SELL higher
            return max(s["BUY"]) < min(s["SELL"])
        return False
    if stype == "IRON_CONDOR":
        if len(resolved) != 4 or len(s["SELL"]) != 2 or len(s["BUY"]) != 2:
            return False
        ordered = sorted(resolved, key=lambda e: e["_contract"].strike)
        seq = "".join("B" if e["action"] == "BUY" else "S" for e in ordered)
        return seq == "BSSB"
    return False


def run_rule_checks(proposal: dict, chain: list, account, exposure_used_pct: float,
                    open_positions_count: int, technical_report: dict | None = None,
                    volatility_report: dict | None = None,
                    symbol_exposure_used_pct: float | None = None,
                    min_dte: int | None = None, max_dte: int | None = None,
                    max_spread_pct: float | None = None,
                    max_loss_pct: float | None = None) -> RuleCheckResult:
    r = RuleCheckResult()

    stype = proposal.get("strategy_type", "WAIT")
    if stype == "WAIT":
        r.checks = {"no_trade_proposal": True}
        r.notes.append("chief proposes WAIT -- nothing to execute")
        return r
    r.checks["entry_style_allowed"] = stype in _ALLOWED_ENTRY_STRATS
    if not r.checks["entry_style_allowed"]:
        r.notes.append(f"strategy {stype} blocked: long call/put only")
        return r

    buying_power = float(account.buying_power or 0)
    equity = float(account.equity or 0)

    # --- resolve every leg against the real chain ---------------------------
    # LLM leg qty is noise (sometimes 100) -- strategies are always 1-unit sets
    resolved, missing = [], []
    for leg in proposal.get("legs", []):
        sym = leg.get("symbol", "")
        exact = next((c for c in chain if c.symbol == sym), None)
        contract = exact
        if contract is None and stype not in _LONG_STRATS:
            contract = resolve_leg(
                chain, leg.get("type") or ("call" if "C" in sym[-9:] else "put"),
                float(leg.get("strike", 0)) or 0, leg.get("expiry", ""),
            )
        if contract is None:
            missing.append(sym)
            continue
        entry = {
            "action": leg.get("action", "BUY").upper(),
            "symbol": contract.symbol,
            "qty": 1,
            "_contract": contract,
            "snap": None if exact else f"{sym}->{contract.symbol}",
        }
        resolved.append(entry)
    r.resolved_legs = [
        {k: v for k, v in e.items() if k != "_contract"} | {
            "strike": e["_contract"].strike,
            "expiry": e["_contract"].expiry,
            "type": e["_contract"].opt_type,
            "mid": e["_contract"].mid,
            "bid": getattr(e["_contract"], "bid", e["_contract"].mid),
            "ask": getattr(e["_contract"], "ask", e["_contract"].mid),
            "spread_pct": e["_contract"].spread_pct,
            "delta": e["_contract"].delta,
            "dte": e["_contract"].dte,
        }
        for e in resolved
    ]
    r.checks["legs_valid"] = len(resolved) == len(proposal.get("legs", [])) and bool(resolved)
    if missing:
        r.notes.append(f"unresolvable legs: {missing}")

    if not resolved:
        r.checks.update({"max_loss_within_limit": False})
        return r

    # --- structural consistency --------------------------------------------
    expiries = {e["_contract"].expiry for e in resolved}
    r.checks["single_expiry"] = len(expiries) == 1
    n_sell = sum(1 for e in resolved if e["action"] == "SELL")
    n_buy = sum(1 for e in resolved if e["action"] == "BUY")
    r.checks["legs_covered"] = (
        (stype in _CREDIT_STRATS and n_sell == n_buy and len(resolved) in (2, 4))
        or (stype in _DEBIT_STRATS and len(resolved) == 2)
        or (stype in _LONG_STRATS and len(resolved) == 1)
    )
    r.checks["structure_consistent"] = _structure_consistent(stype, resolved)

    max_loss_u, max_profit_u, net_u = _unit_metrics(stype, resolved)
    r.max_loss_unit, r.max_profit_unit, r.net_price_unit = max_loss_u, max_profit_u, net_u

    # money sanity: credit strats must net RECEIVE, debit strats must net PAY,
    # and defined-profit structures must actually have positive max profit
    if stype in _CREDIT_STRATS:
        r.checks["net_credit_positive"] = net_u > 0
    elif stype in _DEBIT_STRATS:
        r.checks["net_debit_negative"] = net_u < 0
    if stype not in _LONG_STRATS:
        r.checks["max_profit_positive"] = max_profit_u > 0

    if stype in _LONG_STRATS and technical_report and volatility_report:
        conflict = (
            volatility_report.get("premium_bias") == "SELL_PREMIUM"
            and technical_report.get("alignment") != "FULL"
        )
        r.checks["premium_alignment"] = not conflict
        if conflict:
            r.notes.append("long premium blocked: volatility favors selling and trend is not fully aligned")

    proposed_qty = max(1, int(proposal.get("qty", 1) or 1))

    # --- hard limits ---------------------------------------------------------
    max_loss_budget = equity * (
        config.MAX_LOSS_PCT_PER_TRADE if max_loss_pct is None else max_loss_pct
    )
    fit_qty = int(max_loss_budget // max_loss_u) if max_loss_u > 0 else 0
    r.adjusted_qty = min(proposed_qty, max(fit_qty, 0))
    r.checks["max_loss_within_limit"] = r.adjusted_qty >= 1
    if r.adjusted_qty < proposed_qty:
        r.notes.append(
            f"qty adjusted {proposed_qty}->{r.adjusted_qty} to keep max loss "
            f"<= ${max_loss_budget:.0f}"
        )
    total_max_loss = max_loss_u * max(r.adjusted_qty, 1)

    margin_needed = total_max_loss if stype in (_CREDIT_STRATS, _DEBIT_STRATS, _LONG_STRATS) else 0
    r.checks["buying_power_sufficient"] = margin_needed <= buying_power * 0.5

    if equity <= 0:
        r.checks["buying_power_sufficient"] = False
        r.checks["exposure_within_limit"] = False
        return r
    new_exposure = exposure_used_pct + total_max_loss / equity * 100
    symbol_exposure = (symbol_exposure_used_pct if symbol_exposure_used_pct is not None else exposure_used_pct)
    r.checks["exposure_within_limit"] = (
        symbol_exposure + total_max_loss / equity * 100 <= config.MAX_EXPOSURE_PCT_PER_SYMBOL * 100
        and open_positions_count < config.MAX_OPEN_POSITIONS_TOTAL
    )
    spread_limit = config.MAX_BID_ASK_SPREAD_PCT if max_spread_pct is None else max_spread_pct
    r.checks["liquidity_acceptable"] = all(e["_contract"].spread_pct <= spread_limit for e in resolved)
    min_dte = config.MIN_DTE if min_dte is None else min_dte
    max_dte = config.MAX_DTE if max_dte is None else max_dte
    dtes = [e["_contract"].dte for e in resolved]
    r.checks["dte_within_range"] = (
        bool(dtes) and min(dtes) >= min_dte and max(dtes) <= max_dte
    )

    # earnings guardrail (deterministic): no long premium near earnings
    earn = proposal.get("_earnings_days")
    long_premium_near_earnings = (
        stype in (_LONG_STRATS | {"STRADDLE"}) and earn is not None and earn <= 10
    )
    if long_premium_near_earnings:
        r.checks["earnings_guardrail"] = False
        r.notes.append("long premium blocked: earnings too close (IV crush)")
    return r


class SanityReview(BaseAgent):
    name = "RiskManagerSanityReview"
    system_prompt = (
        "You are a risk manager doing a final qualitative sanity check of an "
        "approved options trade proposal. The deterministic rules ALREADY passed. "
        "Reject only for clear qualitative red flags the rules cannot see "
        "(nonsensical rationale, contradictory signals, extreme event risk). "
        "Answer JSON only."
    )
    schema = {
        "type": "object",
        "properties": {
            "qualitative_pass": {"type": "boolean"},
            "note": {"type": "string"},
        },
        "required": ["qualitative_pass", "note"],
    }

    def fallback(self) -> dict:
        return {"qualitative_pass": True, "note": "LLM sanity unavailable -- rules govern"}

    def _validate(self, report: dict) -> bool:
        # this review has no confidence field -- check only its own contract
        qp = report.get("qualitative_pass")
        if isinstance(qp, str):
            qp = qp.strip().lower() == "true"
        return isinstance(qp, bool) and bool(report.get("note"))

    def review(self, proposal: dict, technical_report: dict, context_report: dict) -> dict:
        payload = {
            "proposal": proposal,
            "technical_report": technical_report,
            "context_report": context_report,
        }
        out = self._call_llm(payload)
        if out and self._validate(out):
            qp = out["qualitative_pass"]
            return {
                "qualitative_pass": bool(qp), "note": str(out["note"]),
                "_usage": dict(self.last_usage),
            }
        fallback = self.fallback()
        fallback["_usage"] = dict(self.last_usage)
        return fallback


def decide(proposal: dict, chain: list, account, exposure_used_pct: float,
           open_positions_count: int, technical_report: dict = None,
           context_report: dict = None, volatility_report: dict = None,
           symbol_exposure_used_pct: float | None = None,
           use_llm_sanity: bool = True, min_dte: int | None = None,
           max_dte: int | None = None, max_spread_pct: float | None = None,
           max_loss_pct: float | None = None) -> dict:
    """Full risk_decision dict per AGENTS.md section 3."""
    r = run_rule_checks(
        proposal, chain, account, exposure_used_pct, open_positions_count,
        technical_report, volatility_report, symbol_exposure_used_pct,
        min_dte, max_dte, max_spread_pct, max_loss_pct,
    )

    hard_checks = {k: v for k, v in r.checks.items()}
    all_pass = all(hard_checks.values())

    decision = "APPROVED" if all_pass else "REJECTED"
    notes = list(r.notes)

    sanity_note = ""
    sanity_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    if all_pass and use_llm_sanity and proposal.get("strategy_type") != "WAIT":
        review = SanityReview().review(proposal, technical_report or {}, context_report or {})
        sanity_note = review.get("note", "")
        sanity_usage = review.get("_usage", sanity_usage)
        if not review.get("qualitative_pass", True):
            decision = "REJECTED"
            notes.append(f"LLM sanity veto: {sanity_note}")

    return {
        "agent": "RiskManagerAgent",
        "decision": decision,
        "checks": hard_checks,
        "adjusted_qty": r.adjusted_qty if proposal.get("strategy_type") != "WAIT" else 0,
        "recomputed": {
            "max_loss_usd_per_unit": r.max_loss_unit,
            "max_profit_usd_per_unit": r.max_profit_unit if r.max_profit_unit != math.inf else None,
            "net_credit_or_debit_per_unit": r.net_price_unit,
            "resolved_legs": r.resolved_legs,
        },
        "notes": "; ".join(notes) if notes else "",
        "llm_sanity_note": sanity_note,
        "_usage": sanity_usage,
    }
