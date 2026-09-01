"""
orchestrator/candidate_builder.py — Deterministic XSP Iron Condor CandidateBuilder.

PRD §3 / §8 rules enforced here:
  - Instrument: XSP only
  - DTE: 1–5 days (Iron Condor default, 1–3 preferred)
  - Short strikes: approximately 16-delta
  - Long strikes: one spread width away from short strikes
  - Structure: Iron Condor (default), Bull Put Spread, Bear Call Spread
  - No fabricated contracts, prices, strikes, or expiry

The CandidateBuilder is the ONLY graph boundary that can supply a TradeProposal.
The Chief selects a candidate by proposal_id; it cannot construct contract data.

Design:
  - Consumes validated evidence already in CycleState (no separate data path)
  - Uses EvidenceGatherer chain data when available
  - Uses only supplied, validated option-chain contracts and quotes
  - Invalid/missing data -> empty list (NOT_READY safe rejection)
  - Never invents symbols, strikes, quotes, IV, max loss, or net credit
"""

from __future__ import annotations

import logging
import math
import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

from data_engine.options_pricing import (
    XSP_STRIKE_INCREMENT,
    SHORT_PUT_DELTA_TARGET,
    SHORT_CALL_DELTA_TARGET,
    MIN_DTE,
    MAX_DTE,
    PREFERRED_DTE,
    nearest_valid_expiry,
)
from orchestrator.boundaries import CandidateBuilder, TradingMandate
from orchestrator.state import CandidateProvenance, CycleState, ValidatedCandidate
from agents.risk_manager import LegSpec, SpreadSpec, TradeProposal, TradeSide

if TYPE_CHECKING:
    from data_engine.quant_engine import EvidenceEnvelope, MarketDataBundle, QuantMetrics

logger = logging.getLogger("aeroquant.candidate_builder")

# XSP options multiplier (same as SPY, 1/10 of SPX)
XSP_MULTIPLIER = 100

# ATM threshold: strike within this fraction of spot is considered ATM
ATM_BAND = 0.02   # 2% either side of spot

# Liquidity guard: bid-ask spread must be < this fraction of mid

# Source snapshots older than this cannot safely generate executable proposals.
MAX_QUOTE_AGE = timedelta(minutes=5)
SUPPORTED_DATA_TIERS = frozenset({"indicative", "opra"})
MAX_SPREAD_PCT = 0.50   # 50% — reject very wide spreads


def _xsp_symbol(expiration: datetime, strike: float, option_type: str) -> str:
    """
    Construct XSP option symbol per OCC / Alpaca conventions.

    Format: XSP{date}{type}{strike:08d}
    Where date is YYMMDD, type is P/C, and strike is OCC thousandths (8 digits).

    Example: XSP260904P00565000 = XSP Sep 4 2026 $566.00 put
    """
    date_str = expiration.strftime("%y%m%d")
    if option_type not in {"put", "call"}:
        raise ValueError(f"unsupported XSP option type: {option_type!r}")
    if not isinstance(strike, (int, float)) or isinstance(strike, bool) or not math.isfinite(strike) or strike <= 0:
        raise ValueError(f"invalid XSP strike: {strike!r}")
    type_char = "P" if option_type == "put" else "C"
    strike_thousandths = int(round(strike * 1000))
    return f"XSP{date_str}{type_char}{strike_thousandths:08d}"


def _bid_ask_mid(
    bid: float | None, ask: float | None
) -> tuple[float, float, float] | None:
    """Return (bid, ask, mid) or None if prices are invalid."""
    if bid is None or ask is None:
        return None
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in (bid, ask)):
        return None
    if bid < 0 or ask <= 0:
        return None
    if bid >= ask:
        return None  # Inverted spread
    mid = (bid + ask) / 2.0
    if not math.isfinite(mid) or mid <= 0:
        return None
    return (bid, ask, mid)


def _parse_utc_timestamp(value: object) -> datetime | None:
    """Parse an actual timezone-aware source timestamp and normalize it to UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_number(value: object) -> float | None:
    """Return a finite real number, rejecting bools and coercion-by-default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None

def _same_timestamp(left: datetime, right: datetime) -> bool:
    """Require a contract observation to belong to the chain snapshot exactly."""
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


class XSPCandidateBuilder(CandidateBuilder):
    """
    Deterministic XSP Iron Condor CandidateBuilder.

    Consumes validated evidence from CycleState and builds Iron Condor candidates
    following PRD §3/§8 rules. The Chief can select a candidate by proposal_id;
    it cannot construct contract data.

    No fabricated data: invalid/missing evidence -> empty list (NOT_READY).

    Args:
        clock: injectable UTC clock used only to reject stale chain snapshots.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        max_quote_age: timedelta = MAX_QUOTE_AGE,
    ) -> None:
        if max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_quote_age = max_quote_age

    def build(
        self,
        *,
        state: CycleState,
        mandate: TradingMandate,
    ) -> list[ValidatedCandidate]:
        """
        Build XSP Iron Condor candidates from validated evidence.

        Returns:
            List of ValidatedCandidate objects, or empty list if path is not ready.
            An empty list produces CANDIDATE_NOT_READY in the graph.
        """
        try:
            return self._build_impl(state, mandate)
        except Exception as exc:
            logger.warning(
                "XSPCandidateBuilder error cycle=%s: %s",
                state.cycle_id,
                exc,
            )
            return []

    def _build_impl(
        self, state: CycleState, mandate: TradingMandate
    ) -> list[ValidatedCandidate]:
        # ---- Validate required evidence ----
        evidence = state.evidence
        if not evidence:
            logger.info("XSPCandidateBuilder: no evidence in state cycle=%s", state.cycle_id)
            return []

        symbol = str(evidence.get("symbol", ""))
        if symbol != "XSP":
            logger.info(
                "XSPCandidateBuilder: symbol=%s != XSP, cycle=%s",
                symbol,
                state.cycle_id,
            )
            return []

        spot_price = _finite_number(evidence.get("spot_price"))
        if spot_price is None or spot_price <= 0:
            logger.info(
                "XSPCandidateBuilder: invalid spot_price=%s cycle=%s",
                spot_price,
                state.cycle_id,
            )
            return []

        iv_atm = _finite_number(evidence.get("iv_atm"))
        if iv_atm is None or iv_atm <= 0:
            logger.info(
                "XSPCandidateBuilder: invalid iv_atm=%s cycle=%s",
                iv_atm,
                state.cycle_id,
            )
            return []

        dte = evidence.get("dte")
        if isinstance(dte, bool) or not isinstance(dte, int) or not 1 <= dte <= MAX_DTE:
            logger.info(
                "XSPCandidateBuilder: invalid dte=%s cycle=%s",
                dte,
                state.cycle_id,
            )
            return []

        trading_allowed, _reason = mandate.is_trading_allowed()
        if not trading_allowed:
            return []

        # ---- Check mandate ----

        # Check IV regime from quant_metrics
        iv_rank = None
        iv_percentile = None
        if state.quant_metrics:
            iv_rank = state.quant_metrics.iv_rank
            iv_percentile = state.quant_metrics.iv_percentile

        if mandate.iv_regime_required() and (
            not isinstance(iv_rank, (int, float))
            or not isinstance(iv_percentile, (int, float))
            or not math.isfinite(iv_rank) or not math.isfinite(iv_percentile)
            or iv_rank < 60 or iv_percentile < 60
        ):
            return []

        # ---- Get options chain ----
        chain_data = evidence.get("options_chain", {})
        return self._build_from_validated_chain(
            state=state, mandate=mandate, spot_price=spot_price,
            evidence_dte=dte, chain_data=chain_data,
        )
    def _build_from_validated_chain(
        self,
        *,
        state: CycleState,
        mandate: TradingMandate,
        spot_price: float,
        evidence_dte: int,
        chain_data: object,
    ) -> list[ValidatedCandidate]:
        """Build one executable iron condor from one observed chain snapshot only."""
        if not isinstance(chain_data, dict):
            return []
        if "iron_condor" not in mandate.allowed_sides():
            return []

        source = chain_data.get("source")
        snapshot_id = chain_data.get("snapshot_id")
        data_tier = chain_data.get("data_tier")
        observed_at = _parse_utc_timestamp(chain_data.get("snapshot_timestamp"))
        if (
            source != "alpaca_options_chain"
            or not isinstance(snapshot_id, str)
            or not snapshot_id.strip()
            or data_tier not in SUPPORTED_DATA_TIERS
            or observed_at is None
        ):
            return []

        now = self._clock()
        if now.tzinfo is None:
            return []
        now = now.astimezone(timezone.utc)
        quote_age = now - observed_at
        if quote_age < timedelta(seconds=-60) or quote_age > self._max_quote_age:
            return []

        quantity = chain_data.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            return []
        put_wing_width = _finite_number(chain_data.get("put_wing_width"))
        call_wing_width = _finite_number(chain_data.get("call_wing_width"))
        if (
            put_wing_width is None
            or call_wing_width is None
            or put_wing_width <= 0
            or call_wing_width <= 0
        ):
            return []

        expirations = chain_data.get("expirations")
        contracts = chain_data.get("contracts")
        if (
            chain_data.get("underlying_symbol") != "XSP"
            or not isinstance(expirations, list)
            or not isinstance(contracts, dict)
        ):
            return []
        mandate_max_dte = mandate.max_dte()
        if isinstance(mandate_max_dte, bool) or not isinstance(mandate_max_dte, int):
            return []
        selected_expiry = nearest_valid_expiry(
            available_expiries=expirations,
            min_dte=MIN_DTE,
            max_dte=min(MAX_DTE, mandate_max_dte),
            preferred_dte=PREFERRED_DTE,
            as_of=observed_at,
        )
        if selected_expiry is None:
            return []
        try:
            expiry = datetime.strptime(selected_expiry, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return []
        actual_dte = (expiry.date() - observed_at.date()).days
        if not MIN_DTE <= actual_dte <= min(MAX_DTE, mandate_max_dte):
            return []
        if evidence_dte != actual_dte:
            return []

        put_contracts = self._validated_contracts(
            contracts=contracts,
            expiry=selected_expiry,
            expiry_dt=expiry,
            option_type="put",
            observed_at=observed_at,
            source=source,
        )
        call_contracts = self._validated_contracts(
            contracts=contracts,
            expiry=selected_expiry,
            expiry_dt=expiry,
            option_type="call",
            observed_at=observed_at,
            source=source,
        )
        put_pair = self._select_exact_wing(
            contracts=put_contracts,
            option_type="put",
            spot=spot_price,
            wing_width=put_wing_width,
        )
        call_pair = self._select_exact_wing(
            contracts=call_contracts,
            option_type="call",
            spot=spot_price,
            wing_width=call_wing_width,
        )
        if put_pair is None or call_pair is None:
            return []
        account_context = self._account_context(state.evidence.get("account_context"))
        if account_context is None:
            return []

        short_put, long_put = put_pair
        short_call, long_call = call_pair
        actual_put_width = short_put["strike"] - long_put["strike"]
        actual_call_width = long_call["strike"] - short_call["strike"]
        if actual_put_width <= 0 or actual_call_width <= 0:
            return []
        legs = [
            self._leg_from_contract(short_put, "SELL", quantity, expiry),
            self._leg_from_contract(long_put, "BUY", quantity, expiry),
            self._leg_from_contract(short_call, "SELL", quantity, expiry),
            self._leg_from_contract(long_call, "BUY", quantity, expiry),
        ]
        return self._make_validated_candidate(
            legs=legs,
            put_width=actual_put_width,
            call_width=actual_call_width,
            quantity=quantity,
            dte=actual_dte,
            state=state,
            account_context=account_context,
            source=source,
            data_tier=data_tier,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
        )

    def _validated_contracts(
        self,
        *,
        contracts: dict,
        expiry: str,
        expiry_dt: datetime,
        option_type: str,
        observed_at: datetime,
        source: str,
    ) -> list[dict]:
        """Return only fully observed contracts from one expiry/type slice."""
        expiry_contracts = contracts.get(expiry)
        if not isinstance(expiry_contracts, dict):
            return []
        by_type = expiry_contracts.get(option_type)
        if not isinstance(by_type, dict):
            return []

        valid: list[dict] = []
        for key, raw in by_type.items():
            if not isinstance(raw, dict):
                continue
            strike = _finite_number(raw.get("strike"))
            try:
                keyed_strike = float(key)
            except (TypeError, ValueError):
                continue
            if (
                strike is None
                or strike <= 0
                or not math.isfinite(keyed_strike)
                or not math.isclose(strike, keyed_strike, rel_tol=0.0, abs_tol=1e-9)
                or raw.get("expiration") != expiry
                or raw.get("option_type") != option_type
                or raw.get("underlying_symbol") != "XSP"
                or raw.get("source") != source
            ):
                continue
            contract_timestamp = _parse_utc_timestamp(raw.get("observation_timestamp"))
            if contract_timestamp is None or not _same_timestamp(contract_timestamp, observed_at):
                continue
            bid_ask_mid = _bid_ask_mid(raw.get("bid"), raw.get("ask"))
            if bid_ask_mid is None:
                continue
            bid, ask, mid = bid_ask_mid
            supplied_mid = raw.get("mid")
            if supplied_mid is not None:
                parsed_mid = _finite_number(supplied_mid)
                if parsed_mid is None or not math.isclose(parsed_mid, mid, rel_tol=0.0, abs_tol=1e-9):
                    continue
            if (ask - bid) / mid > MAX_SPREAD_PCT:
                continue
            delta = _finite_number(raw.get("delta"))
            if delta is None:
                continue
            if (option_type == "put" and not -1.0 < delta < 0.0) or (
                option_type == "call" and not 0.0 < delta < 1.0
            ):
                continue
            supplied_symbol = raw.get("symbol")
            if not isinstance(supplied_symbol, str) or not supplied_symbol:
                continue
            # This validates the supplied OCC symbol; it is never substituted.
            if supplied_symbol != _xsp_symbol(expiry_dt, strike, option_type):
                continue
            valid.append(
                {
                    "symbol": supplied_symbol,
                    "strike": strike,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "delta": delta,
                }
            )
        return valid

    def _select_exact_wing(
        self,
        *,
        contracts: list[dict],
        option_type: str,
        spot: float,
        wing_width: float,
    ) -> tuple[dict, dict] | None:
        """Choose a target-delta short and its exact observed OTM wing."""
        if option_type == "put":
            shorts = [contract for contract in contracts if contract["strike"] < spot]
            target_delta = SHORT_PUT_DELTA_TARGET
            direction = -1.0
        else:
            shorts = [contract for contract in contracts if contract["strike"] > spot]
            target_delta = SHORT_CALL_DELTA_TARGET
            direction = 1.0
        if not shorts:
            return None
        by_strike = {contract["strike"]: contract for contract in contracts}
        for short in sorted(
            shorts, key=lambda contract: (abs(contract["delta"] - target_delta), contract["strike"])
        ):
            long_strike = short["strike"] + direction * wing_width
            for strike, long in by_strike.items():
                if math.isclose(strike, long_strike, rel_tol=0.0, abs_tol=1e-9):
                    return short, long
        return None

    @staticmethod
    def _leg_from_contract(
        contract: dict, action: str, quantity: int, expiry: datetime
    ) -> LegSpec:
        """Make a leg solely from a selected, validated chain contract."""
        return LegSpec(
            symbol=contract["symbol"],
            action=action,
            quantity=quantity,
            strike=contract["strike"],
            expiration=expiry,
            option_type="put" if contract["delta"] < 0 else "call",
            bid_price=contract["bid"],
            ask_price=contract["ask"],
            mid_price=contract["mid"],
        )

    @staticmethod
    def _account_context(raw: object) -> dict[str, float | int] | None:
        """Validate the observed account context required by TradeProposal."""
        if not isinstance(raw, dict):
            return None

        buying_power = _finite_number(raw.get("buying_power"))
        equity = _finite_number(raw.get("equity"))
        current_exposure = _finite_number(raw.get("current_exposure_pct"))
        daily_pnl = _finite_number(raw.get("daily_realized_pnl_pct"))
        weekly_pnl = _finite_number(raw.get("week_realized_pnl_pct"))
        open_positions = raw.get("open_positions")
        consecutive_rejections = raw.get("consecutive_rejections")
        if (
            buying_power is None
            or buying_power <= 0
            or equity is None
            or equity <= 0
            or current_exposure is None
            or not 0.0 <= current_exposure <= 1.0
            or daily_pnl is None
            or weekly_pnl is None
            or isinstance(open_positions, bool)
            or not isinstance(open_positions, int)
            or open_positions < 0
            or isinstance(consecutive_rejections, bool)
            or not isinstance(consecutive_rejections, int)
            or consecutive_rejections < 0
        ):
            return None
        return {
            "buying_power": buying_power,
            "equity": equity,
            "current_exposure_pct": current_exposure,
            "daily_realized_pnl_pct": daily_pnl,
            "week_realized_pnl_pct": weekly_pnl,
            "open_positions": open_positions,
            "consecutive_rejections": consecutive_rejections,
        }

    def _make_validated_candidate(
        self,
        *,
        legs: list[LegSpec],
        put_width: float,
        call_width: float,
        quantity: int,
        dte: int,
        state: CycleState,
        account_context: dict[str, float | int],
        source: str,
        data_tier: str,
        snapshot_id: str,
        observed_at: datetime,
    ) -> list[ValidatedCandidate]:
        """Create one iron-condor proposal from selected observed contracts."""
        if len(legs) != 4 or any(leg.quantity != quantity for leg in legs):
            return []
        if not all(math.isfinite(leg.mid_price) and leg.mid_price > 0 for leg in legs):
            return []

        short_legs = [leg for leg in legs if leg.action == "SELL"]
        long_legs = [leg for leg in legs if leg.action == "BUY"]
        if len(short_legs) != 2 or len(long_legs) != 2:
            return []
        net_credit = sum(leg.mid_price for leg in short_legs) - sum(
            leg.mid_price for leg in long_legs
        )
        max_credit = sum(leg.mid_price for leg in short_legs)
        if not math.isfinite(net_credit) or net_credit <= 0:
            return []

        # Premiums are dollars/share. One XSP contract controls 100 shares, so
        # dollar risk is the wider wing less the total credit, times quantity.
        max_loss = (max(put_width, call_width) - net_credit) * XSP_MULTIPLIER * quantity
        if not math.isfinite(max_loss) or max_loss <= 0:
            return []

        metrics = state.quant_metrics
        iv_rank = _finite_number(metrics.iv_rank) if metrics else None
        iv_percentile = _finite_number(metrics.iv_percentile) if metrics else None
        momentum = _finite_number(metrics.momentum_zscore_20d) if metrics else None
        iv_high_regime = (
            iv_rank is not None
            and iv_percentile is not None
            and iv_rank >= 60.0
            and iv_percentile >= 60.0
        )
        fingerprint = "|".join(
            [state.cycle_id, snapshot_id, str(quantity)]
            + [f"{leg.action}:{leg.symbol}:{leg.mid_price:.10f}" for leg in legs]
        )
        proposal_id = f"xsp-ic-{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}"
        proposal = TradeProposal(
            proposal_id=proposal_id,
            timestamp=observed_at,
            spread=SpreadSpec(
                legs=legs,
                dte=dte,
                net_credit=net_credit,
                max_credit=max_credit,
                max_loss=max_loss,
                side=TradeSide.IRON_CONDOR,
            ),
            account_buying_power=float(account_context["buying_power"]),
            account_equity=float(account_context["equity"]),
            open_positions=int(account_context["open_positions"]),
            current_exposure_pct=float(account_context["current_exposure_pct"]),
            new_trade_exposure_pct=max_loss / float(account_context["buying_power"]),
            daily_realized_pnl_pct=float(account_context["daily_realized_pnl_pct"]),
            week_realized_pnl_pct=float(account_context["week_realized_pnl_pct"]),
            iv_high_regime=iv_high_regime,
            momentum_zscore=momentum,
            consecutive_rejections=int(account_context["consecutive_rejections"]),
        )
        return [
            ValidatedCandidate(
                proposal=proposal,
                provenance=CandidateProvenance(
                    source=source,
                    data_tier=data_tier,
                    underlying_symbol="XSP",
                    snapshot_timestamp=observed_at,
                    contract_snapshot_id=snapshot_id,
                ),
            )
        ]
