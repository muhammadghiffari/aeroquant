"""
agents/risk_manager.py — Deterministic Risk Gate.

PRD §8: The Risk Manager is the ultimate arbiter, not the agent's intelligence.
CLAUDE.md Rule #1: LLM agents never get trading tools.

ARCHITECTURE
-----------
  LLM Agents (propose) → TradeProposal
                           ↓
                     Risk Manager  ← ONLY authorized path to execution
                           ↓
                   OrderIntent / RiskDecision
                           ↓
                    Execution Layer (Alpaca)

No code path may allow an LLM agent to bypass the Risk Manager.

HARD RULES (enforced, not optional):
  - Max loss per trade ≤ max_loss_pct of buying power
  - Spread width computed exactly: width - net_credit
  - Bid-ask spread capped as % of mid
  - Max 6 concurrent positions
  - Max exposure ≤ max_exposure_pct of buying power
  - TP at 50% of max credit (auto-close when credit_collected >= 0.50 * max_credit)
  - SL at 125% of initial credit (exit when credit_collected <= -1.25 * max_credit)
  - DTE 1–5 days; reject 0DTE
  - Halt new entries at -3% daily realized P&L
  - Halt after 5 consecutive Risk Manager rejections (kill switch)
  - Force-close all short legs before expiry (PRD §9 — enforced by execution layer)

The Risk Manager is stateless per evaluation. Accumulated state (consecutive rejections,
daily P&L) lives in a RiskState record managed by the orchestrator/checkpointer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("aeroquant.risk_manager")

RISK_SCHEMA_VERSION = "2.6.0"
SCHEMA_VERSION = RISK_SCHEMA_VERSION  # Alias for compatibility


# ---------------------------------------------------------------------------
# Enums / simple types
# ---------------------------------------------------------------------------

class RiskStatus(str, Enum):
    """Risk gate verdict."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HALTED_GLOBAL = "HALTED_GLOBAL"  # Kill switch triggered


class TradeSide(str, Enum):
    """Iron Condor side (directional bias of the spread)."""

    SHORT_PUT_SPREAD = "short_put_spread"   # Bull Put Spread
    SHORT_CALL_SPREAD = "short_call_spread"  # Bear Call Spread
    IRON_CONDOR = "iron_condor"             # Default non-directional


# ---------------------------------------------------------------------------
# Contracts — Pydantic models crossing plane boundaries
# ---------------------------------------------------------------------------

class LegSpec(BaseModel):
    """Single leg of a multi-leg spread."""

    model_config = {"str_strip_whitespace": True}

    symbol: str = Field(description="Contract symbol, e.g. XSP241205P00550000")
    action: Literal["BUY", "SELL"] = Field(description="BUY or SELL")
    quantity: int = Field(gt=0, description="Number of contracts (positive integer)")
    strike: float = Field(gt=0, description="Strike price in dollars")
    expiration: datetime = Field(description="Contract expiration datetime (UTC)")
    option_type: Literal["call", "put"] = Field(description="call or put")

    # Computed at proposal time from chain snapshot
    bid_price: float = Field(ge=0, description="Bid price at proposal time")
    ask_price: float = Field(ge=0, description="Ask price at proposal time")
    mid_price: float = Field(ge=0, description="Mid price = (bid + ask) / 2")

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as % of mid price. Key liquidity metric."""
        return self.spread / self.mid_price if self.mid_price > 0 else float("inf")

    @property
    def is_short(self) -> bool:
        """True if this leg collects premium (SELL)."""
        return self.action == "SELL"


class SpreadSpec(BaseModel):
    """
    Full spread definition — one atomic trade proposal.

    All legs must be submitted as a single MLEG order (atomic fill).
    No legging in — never split into separate orders.
    """

    model_config = {"str_strip_whitespace": True}

    legs: list[LegSpec] = Field(min_length=2, description="All legs of the spread")

    # DTE
    dte: int = Field(ge=0, le=730, description="Days to expiration")

    # Net credit/debit from the spread
    net_credit: float = Field(
        description="Net credit received (positive) or debit paid (negative). "
                    "Calculated from mid prices at proposal time."
    )
    max_credit: float = Field(
        ge=0,
        description="Maximum potential credit = sum of short-leg credits at open. "
                    "Used for TP/SL thresholds."
    )
    max_loss: float = Field(
        ge=0,
        description="Maximum potential loss = spread_width - net_credit. "
                    "Computed from exact structure, never assumed."
    )

    # Structure type
    side: TradeSide = Field(default=TradeSide.IRON_CONDOR)

    @property
    def short_legs(self) -> list[LegSpec]:
        return [leg for leg in self.legs if leg.is_short]

    @property
    def long_legs(self) -> list[LegSpec]:
        return [leg for leg in self.legs if not leg.is_short]

    @property
    def all_short(self) -> bool:
        """All legs are short — would be naked selling (forbidden)."""
        return all(leg.is_short for leg in self.legs)

    def validate_structure(self) -> list[str]:
        """
        Structural validation (not risk rules).
        Returns list of error messages; empty = valid.
        """
        errors = []

        # Must have at least one long and one short leg (defined-risk check)
        if not self.short_legs:
            errors.append("Spread must have at least one SELL (short) leg")
        if not self.long_legs:
            errors.append("Spread must have at least one BUY (long) leg to cap risk")

        # No naked short options
        if self.all_short:
            errors.append("Naked short options are forbidden — must be a defined-risk spread")

        # DTE must be positive calendar days
        if self.dte < 0:
            errors.append(f"DTE must be non-negative; got {self.dte}")

        # Check for valid bid-ask (spread not inverted)
        for leg in self.legs:
            if leg.bid_price > leg.ask_price:
                errors.append(f"Leg {leg.symbol}: bid {leg.bid_price} > ask {leg.ask_price} (inverted)")
            if leg.ask_price <= 0:
                errors.append(f"Leg {leg.symbol}: ask price must be positive; got {leg.ask_price}")

        return errors


class TradeProposal(BaseModel):
    """
    Input to the Risk Manager — a proposed spread with full context.

    Created by the Chief Strategy Agent; validated and decided by the Risk Manager.
    """

    model_config = {"str_strip_whitespace": True}

    proposal_id: str = Field(description="Unique ID for this proposal (UUID or cycle-based)")
    spread: SpreadSpec = Field(description="The spread being proposed")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Proposal timestamp (UTC)"
    )

    # Account context (required for sizing)
    account_buying_power: float = Field(gt=0, description="Current buying power in dollars")
    account_equity: float = Field(gt=0, description="Current total equity in dollars")

    # Current position state (for exposure + DTE checks)
    open_positions: int = Field(
        ge=0,
        description="Number of currently open positions"
    )
    current_exposure_pct: float = Field(
        ge=0,
        le=1,
        description="Current buying power used as fraction (0.3 = 30% of buying power deployed)"
    )
    new_trade_exposure_pct: float = Field(
        ge=0,
        le=1,
        default=0.0,
        description="Exposure of this new trade as fraction of buying power. "
                    "Derived from spread.max_loss / account_buying_power. "
                    "Risk Manager adds this to current_exposure_pct to enforce max_exposure_pct."
    )

    # Realized P&L (for daily P&L kill switch)
    daily_realized_pnl_pct: float = Field(
        description="Today's realized P&L as % of account equity. "
                    "Negative values indicate losses. Positive = profits."
    )
    # Cumulative realized P&L this week (for emergency stop)
    week_realized_pnl_pct: float = Field(
        description="Cumulative realized P&L this week as % of account equity."
    )

    # IV regime (for entry filter — PRD §5.1)
    iv_high_regime: bool = Field(
        default=False,
        description="True if IV Rank >= 60 AND IV Percentile >= 60"
    )

    # Momentum (for directional override — PRD §5.1)
    momentum_zscore: float | None = Field(
        default=None,
        description="20-day momentum Z-score. Positive = upward momentum."
    )

    # Consecutive rejections this session (for kill switch)
    consecutive_rejections: int = Field(
        ge=0,
        description="Number of consecutive Risk Manager rejections in current session"
    )

    def validate_proposal(self) -> list[str]:
        """Pre-risk structural validation."""
        errors = self.spread.validate_structure()
        if self.open_positions < 0:
            errors.append(f"open_positions must be >= 0; got {self.open_positions}")
        if self.current_exposure_pct < 0 or self.current_exposure_pct > 1:
            errors.append(
                f"current_exposure_pct must be 0–1; got {self.current_exposure_pct}"
            )
        return errors


class RiskDecision(BaseModel):
    """
    Risk Manager output — the authoritative verdict on a TradeProposal.

    Only this object (and its sibling OrderIntent) may reach the execution layer.
    """

    model_config = {"str_strip_whitespace": True}

    schema_version: str = Field(default=RISK_SCHEMA_VERSION)
    proposal_id: str
    decision: RiskStatus
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Decision timestamp (UTC)"
    )

    # If rejected
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable rejection reasons, one per rule violated"
    )
    rule_names: list[str] = Field(
        default_factory=list,
        description="Machine-readable rule identifiers violated"
    )

    # If approved — also produce the OrderIntent
    order_intent: "OrderIntent | None" = Field(
        default=None,
        description="OrderIntent ready for execution layer. None if rejected."
    )

    # Kill switch state
    global_halt: bool = Field(
        default=False,
        description="True if a kill switch has been triggered. All new proposals rejected."
    )
    halt_reason: str | None = Field(
        default=None,
        description="Reason for global halt, if applicable"
    )


class OrderIntent(BaseModel):
    """
    Execution-ready order — approved by the Risk Gate.

    This is the ONLY object that may flow from the reasoning layer to execution.
    Contains everything needed for a deterministic execution-layer service to submit
    a limit order to Alpaca without any further LLM involvement.

    Design: this model knows nothing about Alpaca API internals — the execution
    layer translates OrderIntent → Alpaca API call. This separation ensures the
    Risk Gate's approval is final and not mutable by the execution layer.
    """

    model_config = {"str_strip_whitespace": True}

    schema_version: str = Field(default=RISK_SCHEMA_VERSION)

    # Identity
    intent_id: str = Field(description="Unique intent ID (UUID, separate from proposal_id)")
    proposal_id: str = Field(description="Original proposal that produced this intent")
    account_id: str = Field(description="Alpaca account ID")

    # Execution parameters
    legs: list["LegIntent"] = Field(description="Individual leg intents")
    order_type: Literal["limit"] = Field(
        default="limit",
        description="Always limit — PRD §7"
    )

    # TP/SL parameters
    tp_credit_threshold: float = Field(
        description="Close when credit_collected >= this. TP at 50% of max credit."
    )
    sl_credit_threshold: float = Field(
        description="Exit when credit_collected <= this. SL at 125% of initial credit."
    )

    # MLEG note for execution layer
    mleg_atomic: bool = Field(
        default=True,
        description="Must be submitted as a single MLEG order — never legged in."
    )

    # Timestamps
    approved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime = Field(
        description="Intent expires at this UTC time if not filled. "
                    "Execution layer must cancel if not filled by this time."
    )
    # Force-close deadline (PRD §9) — earliest expiry among all short legs
    force_close_deadline: datetime | None = Field(
        default=None,
        description="Hard deadline to close all short legs before expiry. "
                    "Execution layer enforces this regardless of TP/SL state."
    )

    # Idempotency key for execution layer
    idempotency_key: str = Field(
        description="Unique key for idempotent submission. Use intent_id."
    )

    @property
    def short_legs(self) -> list["LegIntent"]:
        return [leg for leg in self.legs if leg.action == "SELL"]


class LegIntent(BaseModel):
    """Single leg of an OrderIntent."""

    symbol: str
    action: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_class: Literal["bracket", "simple"] = Field(
        default="simple",
        description="'bracket' = has TP/SL legs; 'simple' = no bracket"
    )

    # Limit price (already padded ±5% toward adverse side per PRD §7)
    limit_price: float = Field(ge=0)

    # Expiration for this leg
    expiration: datetime

    # For bracket legs (if order_class == "bracket")
    take_profit_price: float | None = Field(default=None, ge=0)
    stop_loss_price: float | None = Field(default=None, ge=0)

    def validate(self) -> list[str]:
        errors = []
        if self.quantity <= 0:
            errors.append(f"Leg {self.symbol}: quantity must be > 0")
        if self.limit_price <= 0:
            errors.append(f"Leg {self.symbol}: limit_price must be > 0")
        if self.order_class == "bracket":
            if self.take_profit_price is None or self.stop_loss_price is None:
                errors.append(
                    f"Leg {self.symbol}: bracket order requires TP and SL prices"
                )
            if self.take_profit_price is not None and self.stop_loss_price is not None:
                if self.action == "SELL":
                    # TP should be higher than entry for short premium
                    if self.take_profit_price <= self.limit_price:
                        errors.append(
                            f"Leg {self.symbol}: SELL TP ({self.take_profit_price}) "
                            f"should be > limit ({self.limit_price})"
                        )
        return errors


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------

@dataclass
class RiskParams:
    """
    All risk parameters in one place — easy to review, easy to override for tests.
    Defaults match PRD §8.
    """

    # Position sizing
    max_loss_pct: float = 0.02  # Max loss ≤ 2% of buying power per trade
    min_loss_pct: float = 0.003  # Minimum trade size: 0.3% (reject micro-trades)

    # Exposure
    max_exposure_pct: float = 0.60  # Max 60% of buying power deployed at once
    max_concurrent_positions: int = 6  # Max 6 concurrent positions

    # Liquidity (bid-ask spread cap)
    max_spread_pct: float = 0.05  # Reject if any leg's spread > 5% of mid

    # Entry filter
    iv_rank_entry_threshold: float = 60.0  # Per PRD §5.1: >60 is "strong mandate"

    # DTE
    dte_min: int = 1
    dte_max: int = 5

    # TP / SL
    tp_frac: float = 0.50  # 50% of max credit
    sl_frac: float = 1.25  # 125% of initial credit (exit at loss)

    # Kill switches
    max_consecutive_rejections: int = 5  # Halt after 5 consecutive rejections
    daily_pnl_halt_threshold_pct: float = -0.03  # Halt new entries at -3% daily realized P&L
    weekly_pnl_halt_threshold_pct: float = -0.05  # Hard emergency stop at -5% weekly


class RiskManager:
    """
    Deterministic Risk Gate.

    Stateless per invocation. The orchestrator holds accumulated state
    (consecutive rejections, daily P&L) between calls.

    ENTRY POINT: evaluate(proposal, state) → RiskDecision

    NO EXCEPTIONS to any hard rule. No "warnings" that still approve.
    No LLM in this class. No trading API calls.
    """

    def __init__(self, params: RiskParams | None = None) -> None:
        self.params = params or RiskParams()

    def evaluate(
        self,
        proposal: TradeProposal,
        *,
        global_halt: bool = False,
        halt_reason: str | None = None,
    ) -> RiskDecision:
        """
        Main entry point. Returns a RiskDecision.

        Args:
            proposal: The TradeProposal from the Chief Strategy Agent.
            global_halt: True if a kill switch is already active (e.g. from prior call).
            halt_reason: Reason for existing halt.

        Returns:
            RiskDecision — APPROVED (with OrderIntent) or REJECTED (with reasons).
        """
        # --- KILL SWITCH: global halt ---
        if global_halt:
            return RiskDecision(
                proposal_id=proposal.proposal_id,
                decision=RiskStatus.HALTED_GLOBAL,
                global_halt=True,
                halt_reason=halt_reason or "Global halt already active",
                reasons=["Global kill switch is active"],
                rule_names=["GLOBAL_HALT"],
            )

        # --- Pre-risk structural validation ---
        structural_errors = proposal.validate_proposal()
        if structural_errors:
            return RiskDecision(
                proposal_id=proposal.proposal_id,
                decision=RiskStatus.REJECTED,
                reasons=[f"Structural error: {e}" for e in structural_errors],
                rule_names=["STRUCTURAL_VALIDATION"],
            )

        # --- Run all rule checks, collect failures ---
        reasons: list[str] = []
        rule_names: list[str] = []

        self._check_spread_structure(proposal, reasons, rule_names)
        self._check_dte(proposal, reasons, rule_names)
        self._check_max_loss(proposal, reasons, rule_names)
        self._check_liquidity(proposal, reasons, rule_names)
        self._check_exposure(proposal, reasons, rule_names)
        self._check_position_count(proposal, reasons, rule_names)
        self._check_daily_pnl(proposal, reasons, rule_names)
        self._check_weekly_pnl(proposal, reasons, rule_names)
        self._check_kill_switch(proposal, reasons, rule_names)
        self._check_iv_regime(proposal, reasons, rule_names)

        if reasons:
            return RiskDecision(
                proposal_id=proposal.proposal_id,
                decision=RiskStatus.REJECTED,
                reasons=reasons,
                rule_names=rule_names,
            )

        # --- APPROVED ---
        order_intent = self._build_order_intent(proposal)
        return RiskDecision(
            proposal_id=proposal.proposal_id,
            decision=RiskStatus.APPROVED,
            order_intent=order_intent,
        )

    # -------------------------------------------------------------------------
    # Rule checks — each sets reasons + rule_names on failure, never raises
    # -------------------------------------------------------------------------

    def _check_spread_structure(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §3: defined-risk spread only. No naked short legs."""
        spread = p.spread
        if spread.all_short:
            reasons.append("Naked short options are forbidden — must be a defined-risk spread")
            rules.append("DEFINED_RISK_SPREAD")

    def _check_dte(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: DTE 1–5; reject 0DTE."""
        dte = p.spread.dte
        if dte == 0:
            reasons.append(f"0DTE is forbidden (extreme intraday gamma risk). DTE={dte}")
            rules.append("DTE_ZERO_FORBIDDEN")
        elif dte < self.params.dte_min:
            reasons.append(f"DTE {dte} below minimum {self.params.dte_min} days")
            rules.append("DTE_TOO_SHORT")
        elif dte > self.params.dte_max:
            reasons.append(f"DTE {dte} exceeds maximum {self.params.dte_max} days")
            rules.append("DTE_TOO_LONG")

    def _compute_max_loss_from_legs(self, spread: SpreadSpec) -> float:
        """
        Compute total max loss deterministically from the actual spread structure.

        PRD §8: "Max loss computed exactly from width − net credit. Risk is calculated, never assumed."
        This method is the authoritative calculation. It uses the actual leg strikes and
        quantities, not the LLM-supplied spread.max_loss field.

        Iron Condor structure:
          Put side:  short put (higher strike) - long put (lower strike)
          Call side: short call (lower strike) - long call (higher strike)
          Each side is an independent short vertical spread.

        Formula per side:
          spread_width = |short_strike - long_strike|
          credit_received = short_mid × qty - long_mid × qty
          max_loss_per_side = (spread_width × 100 × qty) - credit_received

        Total max loss = sum of max_loss per side.
        """
        # Separate legs by option type
        put_legs  = [l for l in spread.legs if l.option_type == "put"]
        call_legs = [l for l in spread.legs if l.option_type == "call"]

        def compute_one_side(option_legs: list[LegSpec]) -> float:
            """Compute max loss for one side of the Iron Condor."""
            if not option_legs:
                return 0.0
            shorts = [l for l in option_legs if l.is_short]
            longs  = [l for l in option_legs if not l.is_short]
            if not shorts or not longs:
                return 0.0

            # Spread width: absolute difference between short and long strike
            short_strike = shorts[0].strike
            long_strike  = longs[0].strike
            spread_width = abs(short_strike - long_strike)  # e.g., 555 - 545 = 10.0

            # Quantity is determined by the short legs (that's our defined-risk exposure)
            qty = shorts[0].quantity

            # Net credit = short premium received - long premium paid (per contract)
            short_credit_per_contract = sum(l.mid_price * l.quantity for l in shorts) / qty
            long_debit_per_contract  = sum(l.mid_price * l.quantity for l in longs)  / qty
            net_credit = short_credit_per_contract - long_debit_per_contract

            # Max loss = (spread_width × $100 × qty) - (net_credit × qty)
            # spread_width × $100 = dollar width per contract (e.g., 10pt × $100 = $1,000)
            max_loss = (spread_width * 100.0 * qty) - (net_credit * qty)
            return max_loss

        total = compute_one_side(put_legs) + compute_one_side(call_legs)
        return float(total)

    def _check_max_loss(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """
        PRD §8: max loss per trade ≤ max_loss_pct of buying power.
        Max loss is computed deterministically from actual leg strikes and quantities.
        The LLM-supplied spread.max_loss is compared against the computed value —
        significant discrepancy (>5%) is logged as a warning.
        """
        computed_max_loss = self._compute_max_loss_from_legs(p.spread)
        llm_supplied_max_loss = p.spread.max_loss

        # Flag if LLM-supplied value diverges significantly from our computation
        if llm_supplied_max_loss > 0 and computed_max_loss > 0:
            discrepancy = abs(computed_max_loss - llm_supplied_max_loss) / computed_max_loss
            if discrepancy > 0.05:
                pct_str = f"{discrepancy * 100:.1f}%"
                logger.warning(
                    "max_loss_discrepancy proposal=%s computed=%.2f supplied=%.2f pct=%s",
                    p.proposal_id, computed_max_loss, llm_supplied_max_loss, pct_str
                )

        # Use the computed value as authoritative
        max_loss = computed_max_loss
        max_loss_dollar = p.account_buying_power * self.params.max_loss_pct

        if max_loss > max_loss_dollar:
            reasons.append(
                f"Max loss ${max_loss:.2f} exceeds ${max_loss_dollar:.2f} "
                f"({self.params.max_loss_pct:.1%} of buying power). "
                f"Reduce position size or widen/shorten strikes."
            )
            rules.append("MAX_LOSS_EXCEEDED")

    def _check_liquidity(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: bid-ask spread capped as % of mid."""
        for leg in p.spread.legs:
            if leg.mid_price <= 0:
                reasons.append(f"Leg {leg.symbol}: mid_price is zero or negative ({leg.mid_price})")
                rules.append("INVALID_PRICE")
                continue
            spread_pct = leg.spread / leg.mid_price
            if spread_pct > self.params.max_spread_pct:
                reasons.append(
                    f"Leg {leg.symbol}: bid-ask spread {spread_pct:.1%} exceeds "
                    f"{self.params.max_spread_pct:.1%} of mid (bid={leg.bid_price}, "
                    f"ask={leg.ask_price}, mid={leg.mid_price:.4f}). "
                    f"Low liquidity — slippage risk."
                )
                rules.append("LIQUIDITY_INSUFFICIENT")
                break  # One failure is enough

    def _check_exposure(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: current + new trade exposure ≤ max_exposure_pct of buying power."""
        total_exposure = p.current_exposure_pct + p.new_trade_exposure_pct
        if total_exposure > self.params.max_exposure_pct:
            reasons.append(
                f"Total exposure {total_exposure:.1%} "
                f"(current {p.current_exposure_pct:.1%} + new trade {p.new_trade_exposure_pct:.1%}) "
                f"exceeds {self.params.max_exposure_pct:.0%} limit."
            )
            rules.append("MAX_EXPOSURE_EXCEEDED")

    def _check_position_count(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: max 6 concurrent positions."""
        if p.open_positions >= self.params.max_concurrent_positions:
            reasons.append(
                f"Already at max concurrent positions ({p.open_positions}). "
                f"New entry rejected until a position closes."
            )
            rules.append("MAX_POSITIONS_EXCEEDED")

    def _check_daily_pnl(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: halt new entries at -3% daily realized P&L."""
        if p.daily_realized_pnl_pct <= self.params.daily_pnl_halt_threshold_pct:
            reasons.append(
                f"Daily realized P&L {p.daily_realized_pnl_pct:.2%} has breached "
                f"the -{self.params.daily_pnl_halt_threshold_pct:.0%} halt threshold. "
                f"No new entries until P&L recovers."
            )
            rules.append("DAILY_PNL_HALT")

    def _check_weekly_pnl(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: emergency stop at -5% weekly."""
        if p.week_realized_pnl_pct <= self.params.weekly_pnl_halt_threshold_pct:
            reasons.append(
                f"Weekly realized P&L {p.week_realized_pnl_pct:.2%} has breached "
                f"the -{self.params.weekly_pnl_halt_threshold_pct:.0%} emergency stop. "
                f"ALL trading halted."
            )
            rules.append("WEEKLY_PNL_EMERGENCY_STOP")

    def _check_kill_switch(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """PRD §8: halt after 5 consecutive Risk Manager rejections."""
        if p.consecutive_rejections >= self.params.max_consecutive_rejections:
            reasons.append(
                f"Consecutive rejection count {p.consecutive_rejections} >= "
                f"{self.params.max_consecutive_rejections}. Kill switch triggered. "
                f"No new entries this session."
            )
            rules.append("CONSECUTIVE_REJECTION_KILL_SWITCH")

    def _check_iv_regime(self, p: TradeProposal, reasons: list, rules: list) -> None:
        """
        PRD §5.1 / §8: IV Rank >60 is the "strongest mandate" to authorize short-premium.
        This is a SOFT gate — if IV Rank/Percentile are both below threshold, the trade
        is still allowed but flagged. The PRD says "treat as low-confidence until 10+
        trading sessions exist" — we don't reject on this, we just log it.
        The hard entry filter is DTE, max_loss, liquidity, exposure.
        """
        # IV regime is advisory — not a hard rejection criterion
        # The PRD §5.1 language "low-confidence" means agents should weight the signal
        # differently, not that the Risk Gate must reject. We log and continue.
        if not p.iv_high_regime:
            logger.info(
                "iv_regime_advisory proposal=%s iv_high_regime=False — "
                "short-premium rationale is weaker. Proceeding with risk checks only.",
                p.proposal_id
            )

    # -------------------------------------------------------------------------
    # OrderIntent construction (only on APPROVED)
    # -------------------------------------------------------------------------

    def _build_order_intent(self, proposal: TradeProposal) -> OrderIntent:
        """Construct an execution-ready OrderIntent from an approved proposal."""
        spread = proposal.spread
        params = self.params

        # TP/SL thresholds based on max credit
        tp_threshold = params.tp_frac * spread.max_credit  # Close at 50% of max credit
        sl_threshold = -params.sl_frac * spread.max_credit  # Exit at -125% of max credit

        # Limit price: pad ±5% toward adverse side per PRD §7
        # For SELL legs: pad bid DOWN (lower = more conservative)
        # For BUY legs: pad ask UP (higher = more conservative)
        leg_intents = []
        for leg in spread.legs:
            if leg.action == "SELL":
                # Adverse side is the bid (we're selling, buyer pays bid)
                # Pad 5% toward adverse = lower the limit price
                limit = leg.bid_price * 0.95
            else:
                # Adverse side is the ask (we're buying, we pay ask)
                # Pad 5% toward adverse = raise the limit price
                limit = leg.ask_price * 1.05

            leg_intent = LegIntent(
                symbol=leg.symbol,
                action=leg.action,
                quantity=leg.quantity,
                order_class="simple",  # TP/SL managed at the spread level, not per-leg bracket
                limit_price=round(limit, 4),
                expiration=leg.expiration,
            )
            leg_intents.append(leg_intent)

        # Force-close deadline: earliest expiry among all SHORT legs (PRD §9)
        # Execution layer must close before this regardless of TP/SL state
        short_expirations = [
            leg.expiration for leg in spread.legs if leg.is_short
        ]
        force_close = (
            min(short_expirations) if short_expirations else None
        )

        return OrderIntent(
            intent_id=f"intent-{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            account_id="",  # Set by orchestrator before execution
            legs=leg_intents,
            tp_credit_threshold=round(tp_threshold, 4),
            sl_credit_threshold=round(sl_threshold, 4),
            mleg_atomic=True,
            # Execution layer sets the actual order expiry / good_till_canceled.
            # Set to 5 minutes in future so this OrderIntent is never invalid at submission.
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            force_close_deadline=force_close,
            idempotency_key=f"intent-{proposal.proposal_id}",
        )
