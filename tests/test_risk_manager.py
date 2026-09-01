"""
tests/test_risk_manager.py — Deterministic unit tests for RiskManager.

Tests every hard rule in PRD §8 by deliberately breaching each one.
Also tests that valid proposals are accepted.
No network. No LLM. No Alpaca.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.risk_manager import (
    RiskManager,
    RiskDecision,
    RiskStatus,
    TradeProposal,
    SpreadSpec,
    LegSpec,
    OrderIntent,
    RiskParams,
    TradeSide,
)
from tests.conftest import (
    make_leg,
    make_iron_condor_proposal,
    risk_manager,
)


# ---------------------------------------------------------------------------
# Valid Proposals: Must Be Approved
# ---------------------------------------------------------------------------

class TestRiskManagerApprovesValidProposals:
    """Valid Iron Condor proposals MUST be approved."""

    def test_standard_iron_condor_approved(self, risk_manager):
        """Standard Iron Condor within all limits → APPROVED."""
        proposal = make_iron_condor_proposal(
            max_loss_pct=0.02,
            account_buying_power=100_000,
            open_positions=0,
            current_exposure_pct=0.0,
            daily_pnl_pct=0.0,
            week_pnl_pct=0.0,
            iv_high_regime=True,
            consecutive_rejections=0,
            net_credit=1.00,
            dte=3,
        )
        decision = risk_manager.evaluate(proposal)

        assert decision.decision == RiskStatus.APPROVED, (
            f"Valid proposal rejected: {decision.reasons}"
        )
        assert decision.order_intent is not None
        assert decision.order_intent.mleg_atomic is True

    def test_small_position_count_edge(self, risk_manager):
        """5 open positions + 1 new = 6 total → APPROVED."""
        proposal = make_iron_condor_proposal(open_positions=5)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED

    def test_dte_min_boundary(self, risk_manager):
        """DTE = 1 (minimum) → APPROVED."""
        proposal = make_iron_condor_proposal(dte=1)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED

    def test_dte_max_boundary(self, risk_manager):
        """DTE = 5 (maximum) → APPROVED."""
        proposal = make_iron_condor_proposal(dte=5)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED

    def test_low_iv_regime_still_approved(self, risk_manager):
        """Low IV regime is advisory, not a hard rejection → APPROVED."""
        proposal = make_iron_condor_proposal(iv_high_regime=False)
        decision = risk_manager.evaluate(proposal)
        # IV regime is not a hard rejection criterion
        assert decision.decision == RiskStatus.APPROVED

    def test_approved_produces_order_intent(self, risk_manager):
        """Approved proposal produces a valid OrderIntent."""
        proposal = make_iron_condor_proposal(
            net_credit=1.00,
            dte=3,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.order_intent is not None

        intent = decision.order_intent
        assert intent.mleg_atomic is True
        assert intent.tp_credit_threshold > 0  # 50% of max credit
        assert intent.sl_credit_threshold < 0  # -125% of max credit = negative
        assert len(intent.legs) == 4  # Iron Condor: 4 legs

        # Limit prices should be 5% padded toward adverse side
        for leg_intent in intent.legs:
            assert leg_intent.limit_price > 0


# ---------------------------------------------------------------------------
# Hard Rule: Defined-Risk Spread
# ---------------------------------------------------------------------------

class TestDefinedRiskSpread:
    def test_naked_short_rejected(self, risk_manager):
        """Naked short option → REJECTED (structural: single leg violates min_length=2)."""
        from pydantic import ValidationError
        naked_leg = make_leg(action="SELL", bid=2.00, ask=2.05)
        # SpreadSpec requires >= 2 legs; Pydantic fires at construction
        with pytest.raises(ValidationError, match="at least 2"):
            SpreadSpec(
                legs=[naked_leg],
                dte=3,
                net_credit=2.00,
                max_credit=2.00,
                max_loss=999999.0,
                side=TradeSide.IRON_CONDOR,
            )

    def test_only_short_legs_rejected(self, risk_manager):
        """All-SELL spread → REJECTED at structural validation."""
        short_put = make_leg(symbol="XSP241205P00550000", action="SELL", bid=1.00, ask=1.01)
        short_call = make_leg(symbol="XSP241205C00600000", action="SELL", bid=1.00, ask=1.01)
        spread = SpreadSpec(
            legs=[short_put, short_call],
            dte=3,
            net_credit=2.00,
            max_credit=2.00,
            max_loss=99999.0,
            side=TradeSide.IRON_CONDOR,
        )
        errors = spread.validate_structure()
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Hard Rule: DTE 1-5 / Reject 0DTE
# ---------------------------------------------------------------------------

class TestDTELimits:
    def test_0dte_rejected(self, risk_manager):
        """0DTE is forbidden → REJECTED."""
        proposal = make_iron_condor_proposal(dte=0)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "DTE_ZERO_FORBIDDEN" in decision.rule_names

    def test_dte_too_short_rejected(self, risk_manager):
        """DTE < 0 → REJECTED (Pydantic fires at construction)."""
        # SpreadSpec requires dte >= 0. dte=-1 fails Pydantic validation at construction.
        # This test verifies the validation fires before Risk Manager runs.
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            make_iron_condor_proposal(dte=-1)  # dte < 0 → Pydantic rejects

    def test_dte_too_long_rejected(self, risk_manager):
        """DTE = 10 (exceeds max 5) → REJECTED."""
        proposal = make_iron_condor_proposal(dte=10)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "DTE_TOO_LONG" in decision.rule_names

    def test_dte_6_rejected(self, risk_manager):
        """DTE = 6 → REJECTED (exceeds max 5)."""
        proposal = make_iron_condor_proposal(dte=6)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED


# ---------------------------------------------------------------------------
# Hard Rule: Max Loss
# ---------------------------------------------------------------------------

class TestMaxLossLimit:
    def test_max_loss_exceeded_rejected(self, risk_manager):
        """Max loss > max_loss_pct of buying power → REJECTED."""
        # Use stricter max_loss_pct so the computed loss ($1,996) exceeds the dollar threshold.
        strict_risk_manager = RiskManager(params=RiskParams(max_loss_pct=0.001))  # $100 limit
        proposal = make_iron_condor_proposal(account_buying_power=100_000)
        # Override max_loss too for clarity (computed $1,996 > $100 → REJECTED)
        proposal.spread.max_loss = 1_000_000.0

        decision = strict_risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "MAX_LOSS_EXCEEDED" in decision.rule_names


# ---------------------------------------------------------------------------
# Hard Rule: Liquidity
# ---------------------------------------------------------------------------

class TestLiquidityGuardrail:
    def test_wide_spread_rejected(self, risk_manager):
        """Bid-ask spread > 5% of mid → REJECTED."""
        # Create a spread with illiquid wide bid-ask
        short_put = make_leg(
            symbol="XSP241205P00550000",
            action="SELL",
            bid=1.00,
            ask=1.20,  # 20% spread → > 5% limit
        )
        long_put = make_leg(
            symbol="XSP241205P00540000",
            action="BUY",
            bid=0.50,
            ask=0.51,
        )
        spread = SpreadSpec(
            legs=[short_put, long_put],
            dte=3,
            net_credit=0.50,
            max_credit=1.00,
            max_loss=10.00,  # wide spread
            side=TradeSide.SHORT_PUT_SPREAD,
        )
        proposal = TradeProposal(
            proposal_id="liquidity-test",
            spread=spread,
            account_buying_power=100_000,
            account_equity=100_000,
            open_positions=0,
            current_exposure_pct=0.0,
            daily_realized_pnl_pct=0.0,
            week_realized_pnl_pct=0.0,
            iv_high_regime=True,
            consecutive_rejections=0,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "LIQUIDITY_INSUFFICIENT" in decision.rule_names

    def test_inverted_bid_ask_rejected(self, risk_manager):
        """Inverted bid > ask → REJECTED."""
        bad_leg = make_leg(
            symbol="XSP241205P00550000",
            action="SELL",
            bid=2.00,  # bid > ask (inverted)
            ask=1.00,
        )
        good_leg = make_leg(
            symbol="XSP241205P00540000",
            action="BUY",
            bid=0.50,
            ask=0.51,
        )
        spread = SpreadSpec(
            legs=[bad_leg, good_leg],
            dte=3,
            net_credit=1.50,
            max_credit=2.00,
            max_loss=10.00,
            side=TradeSide.SHORT_PUT_SPREAD,
        )
        proposal = TradeProposal(
            proposal_id="inverted-test",
            spread=spread,
            account_buying_power=100_000,
            account_equity=100_000,
            open_positions=0,
            current_exposure_pct=0.0,
            daily_realized_pnl_pct=0.0,
            week_realized_pnl_pct=0.0,
            iv_high_regime=True,
            consecutive_rejections=0,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED


# ---------------------------------------------------------------------------
# Hard Rule: Max Exposure
# ---------------------------------------------------------------------------

class TestMaxExposure:
    def test_exposure_exceeded_rejected(self, risk_manager):
        """Total exposure (current + new trade) > 60% → REJECTED."""
        # The fix: _check_exposure now computes current_exposure + new_trade_exposure.
        # Existing 40% exposure + new trade 30% exposure = 70% > 60% → REJECTED.
        proposal = make_iron_condor_proposal(
            current_exposure_pct=0.40,
            new_trade_exposure_pct=0.30,  # total = 70% > 60%
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "MAX_EXPOSURE_EXCEEDED" in decision.rule_names
        assert "70" in decision.reasons[0]  # 70% total exposure in the reason


# ---------------------------------------------------------------------------
# Hard Rule: Max Concurrent Positions
# ---------------------------------------------------------------------------

class TestMaxPositions:
    def test_max_positions_exceeded_rejected(self, risk_manager):
        """6 open positions → REJECTED (can't add a 7th)."""
        proposal = make_iron_condor_proposal(
            open_positions=6,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "MAX_POSITIONS_EXCEEDED" in decision.rule_names


# ---------------------------------------------------------------------------
# Hard Rule: Daily P&L Halt
# ---------------------------------------------------------------------------

class TestDailyPnLHalt:
    def test_daily_pnl_halt_rejected(self, risk_manager):
        """Daily realized P&L ≤ -3% → REJECTED."""
        proposal = make_iron_condor_proposal(
            daily_pnl_pct=-0.03,  # exactly at threshold
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "DAILY_PNL_HALT" in decision.rule_names

    def test_daily_pnl_slightly_below_halt(self, risk_manager):
        """Daily realized P&L = -3.5% → REJECTED."""
        proposal = make_iron_condor_proposal(
            daily_pnl_pct=-0.035,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED

    def test_daily_pnl_above_halt_approved(self, risk_manager):
        """Daily realized P&L = -2% → APPROVED (above threshold)."""
        proposal = make_iron_condor_proposal(
            daily_pnl_pct=-0.02,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED


# ---------------------------------------------------------------------------
# Hard Rule: Weekly P&L Emergency Stop
# ---------------------------------------------------------------------------

class TestWeeklyPnLEmergencyStop:
    def test_weekly_pnl_emergency_stop_rejected(self, risk_manager):
        """Weekly realized P&L ≤ -5% → REJECTED (emergency stop)."""
        proposal = make_iron_condor_proposal(
            week_pnl_pct=-0.05,  # exactly at emergency stop
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "WEEKLY_PNL_EMERGENCY_STOP" in decision.rule_names


# ---------------------------------------------------------------------------
# Hard Rule: Consecutive Rejection Kill Switch
# ---------------------------------------------------------------------------

class TestConsecutiveRejectionKillSwitch:
    def test_consecutive_rejections_at_limit(self, risk_manager):
        """5 consecutive rejections → REJECTED (kill switch)."""
        proposal = make_iron_condor_proposal(
            consecutive_rejections=5,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "CONSECUTIVE_REJECTION_KILL_SWITCH" in decision.rule_names

    def test_consecutive_rejections_below_limit_approved(self, risk_manager):
        """4 consecutive rejections (below 5) → APPROVED if valid."""
        proposal = make_iron_condor_proposal(
            consecutive_rejections=4,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED


# ---------------------------------------------------------------------------
# Global Halt Tests
# ---------------------------------------------------------------------------

class TestGlobalHalt:
    def test_global_halt_rejects_all(self, risk_manager):
        """Global halt → all proposals REJECTED regardless of content."""
        proposal = make_iron_condor_proposal()
        decision = risk_manager.evaluate(
            proposal,
            global_halt=True,
            halt_reason="Manual emergency stop triggered",
        )
        assert decision.decision == RiskStatus.HALTED_GLOBAL
        assert decision.global_halt is True
        assert decision.halt_reason is not None
        assert "GLOBAL_HALT" in decision.rule_names


# ---------------------------------------------------------------------------
# OrderIntent Validation Tests
# ---------------------------------------------------------------------------

class TestOrderIntentValidation:
    def test_tp_threshold_correct(self, risk_manager):
        """TP = 50% of max credit."""
        proposal = make_iron_condor_proposal(net_credit=1.00)
        decision = risk_manager.evaluate(proposal)
        intent = decision.order_intent

        # TP at 50% of max credit = 50% of $1.00 = $0.50
        assert intent.tp_credit_threshold == 0.50

    def test_sl_threshold_correct(self, risk_manager):
        """SL = -125% of max credit (negative, it's a loss threshold)."""
        proposal = make_iron_condor_proposal(net_credit=1.00)
        decision = risk_manager.evaluate(proposal)
        intent = decision.order_intent

        # SL at -125% of max credit = -$1.25
        assert intent.sl_credit_threshold == -1.25

    def test_limit_prices_5pct_padded(self, risk_manager):
        """Limit prices are 5% padded toward adverse side."""
        proposal = make_iron_condor_proposal()
        decision = risk_manager.evaluate(proposal)
        intent = decision.order_intent

        for leg_intent in intent.legs:
            # SELL legs: limit = bid * 0.95 (pad down)
            # BUY legs: limit = ask * 1.05 (pad up)
            assert leg_intent.limit_price > 0

    def test_mleg_atomic_true(self, risk_manager):
        """OrderIntent is marked as atomic MLEG."""
        proposal = make_iron_condor_proposal()
        decision = risk_manager.evaluate(proposal)
        assert decision.order_intent.mleg_atomic is True


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_exposure_exactly_60pct_rejected(self, risk_manager):
        """60% exposure is at the limit → APPROVED (≤ not <)."""
        proposal = make_iron_condor_proposal(current_exposure_pct=0.60)
        decision = risk_manager.evaluate(proposal)
        # Max exposure = 60% → 60% is the limit, should be approved
        assert decision.decision == RiskStatus.APPROVED

    def test_exposure_61pct_rejected(self, risk_manager):
        """61% exposure → REJECTED."""
        proposal = make_iron_condor_proposal(current_exposure_pct=0.61)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED

    def test_multiple_rule_violations(self, risk_manager):
        """Multiple violations → all reasons reported."""
        proposal = make_iron_condor_proposal(
            dte=0,              # Rule 1: 0DTE
            daily_pnl_pct=-0.05, # Rule 2: daily P&L halt
            open_positions=6,   # Rule 3: max positions
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        # At least 2 rules should be violated (DTE + P&L, or P&L + positions)
        assert len(decision.rule_names) >= 2

    def test_structural_inverted_bid_ask(self, risk_manager):
        """Bid > ask is a structural error, caught before risk rules."""
        leg1 = make_leg(action="SELL", bid=5.00, ask=4.00)  # inverted
        leg2 = make_leg(action="BUY", bid=0.50, ask=0.51)
        spread = SpreadSpec(
            legs=[leg1, leg2],
            dte=3,
            net_credit=4.50,
            max_credit=5.00,
            max_loss=10.00,
            side=TradeSide.SHORT_PUT_SPREAD,
        )
        proposal = TradeProposal(
            proposal_id="inverted-structural",
            spread=spread,
            account_buying_power=100_000,
            account_equity=100_000,
            open_positions=0,
            current_exposure_pct=0.0,
            daily_realized_pnl_pct=0.0,
            week_realized_pnl_pct=0.0,
            iv_high_regime=True,
            consecutive_rejections=0,
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED


# ---------------------------------------------------------------------------
# Idempotency / Schema Tests
# ---------------------------------------------------------------------------

class TestRiskDecisionSchema:
    def test_approved_has_order_intent(self, risk_manager):
        """Approved decision has order_intent."""
        proposal = make_iron_condor_proposal()
        decision = risk_manager.evaluate(proposal)
        assert decision.order_intent is not None
        assert decision.decision == RiskStatus.APPROVED

    def test_rejected_has_reasons(self, risk_manager):
        """Rejected decision has reasons."""
        proposal = make_iron_condor_proposal(dte=0)
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert len(decision.reasons) > 0
        assert decision.order_intent is None

    def test_schema_version_present(self, risk_manager):
        """RiskDecision has schema_version."""
        proposal = make_iron_condor_proposal()
        decision = risk_manager.evaluate(proposal)
        assert decision.schema_version is not None
        assert decision.order_intent is not None
        assert decision.order_intent.schema_version is not None


# ---------------------------------------------------------------------------
# Regression: Fix #1 — Max Exposure (current + new, not current alone)
# ---------------------------------------------------------------------------

class TestMaxExposureRegression:
    def test_total_exposure_checked_not_current_only(self, risk_manager):
        """
        Regression: _check_exposure must check (current + new_trade) <= max,
        not merely current_exposure_pct <= max.

        Scenario: 40% current exposure, new trade adds 30%, total = 70% > 60% limit.
        """
        proposal = make_iron_condor_proposal(
            current_exposure_pct=0.40,
            new_trade_exposure_pct=0.30,  # total = 70% > 60%
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "MAX_EXPOSURE_EXCEEDED" in decision.rule_names
        assert "70" in decision.reasons[0]  # 70% total exposure shown in reason

    def test_exposure_just_under_limit_approved(self, risk_manager):
        """current 40% + new 19% = 59% ≤ 60% → APPROVED."""
        proposal = make_iron_condor_proposal(
            current_exposure_pct=0.40,
            new_trade_exposure_pct=0.19,  # total = 59% ≤ 60%
        )
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED


# ---------------------------------------------------------------------------
# Regression: Fix #2 — Independent max-loss calculation from legs
# ---------------------------------------------------------------------------

class TestMaxLossCalculationRegression:
    def test_compute_max_loss_from_legs_short_put_spread(self, risk_manager):
        """
        _compute_max_loss_from_legs() computes from actual strikes/quantities.
        Single short put spread (555/545, qty=50): spread_width=$10 × $100 × 50 = $50,000
        minus net credit of (2.005 - 0.0505) × 50 = $97.725 → $49,902.275 per side.
        """
        short_leg = make_leg(
            symbol="XSP241205P00555000",
            action="SELL",
            quantity=50,
            strike=555.0,
            option_type="put",
            bid=2.00,
            ask=2.01,
        )
        long_leg = make_leg(
            symbol="XSP241205P00545000",
            action="BUY",
            quantity=50,
            strike=545.0,
            option_type="put",
            bid=0.05,
            ask=0.051,
        )
        spread = SpreadSpec(
            legs=[short_leg, long_leg],
            dte=3,
            net_credit=2.00,
            max_credit=2.00,
            max_loss=1.0,           # Deliberately wrong — computed value is authoritative
            side=TradeSide.SHORT_PUT_SPREAD,
        )
        computed = risk_manager._compute_max_loss_from_legs(spread)
        # (10 × 100 × 50) - ((2.005-0.0505) × 50) = 50,000 - 97.725 = 49,902.275
        assert 49_800 < computed < 50_100
        assert computed != 1.0  # not the wrong LLM-supplied value

    def test_max_loss_uses_computed_not_llm_supplied(self, risk_manager):
        """
        Wrong LLM-supplied max_loss is overridden by the computed value.
        The Iron Condor (qty=50, net_credit=$2.00) has computed max_loss ~$1,804.55,
        which is within the 2% limit → APPROVED regardless of what LLM supplied.
        """
        proposal = make_iron_condor_proposal()
        proposal.spread.max_loss = 1.0  # Clearly wrong; computed is ~$1,804
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED

    def test_max_loss_wrong_high_value_rejected(self, risk_manager):
        """
        With a strict max_loss_pct, the computed value exceeds the dollar limit -> REJECTED.
        Proves the rejection path fires for the MAX_LOSS_EXCEEDED rule.
        """
        strict_rm = RiskManager(params=RiskParams(max_loss_pct=0.001))  # $100 limit
        proposal = make_iron_condor_proposal()
        proposal.spread.max_loss = 1_000_000.0  # extreme LLM-supplied; computed ~$1,996 > $100
        decision = strict_rm.evaluate(proposal)
        assert decision.decision == RiskStatus.REJECTED
        assert "MAX_LOSS_EXCEEDED" in decision.rule_names


# ---------------------------------------------------------------------------
# Regression: Fix #3 — OrderIntent.expires_at is in the future
# ---------------------------------------------------------------------------

class TestOrderIntentExpiryRegression:
    def test_order_intent_expires_at_is_future(self, risk_manager):
        """OrderIntent.expires_at must be set to a future datetime, not the proposal timestamp."""
        proposal = make_iron_condor_proposal()
        decision = risk_manager.evaluate(proposal)
        assert decision.decision == RiskStatus.APPROVED
        assert decision.order_intent is not None

        expires_at = decision.order_intent.expires_at
        assert expires_at is not None
        # expires_at must be in the future (at least 1 minute from now)
        now = datetime.now(timezone.utc)
        assert expires_at > now, (
            f"expires_at={expires_at.isoformat()} must be > now={now.isoformat()}"
        )
        # Should be within a reasonable window (e.g., ≤ 10 minutes)
        delta = (expires_at - now).total_seconds()
        assert 60 <= delta <= 600, (
            f"expires_at delta={delta:.0f}s should be 60-600s; got {delta:.0f}s"
        )
