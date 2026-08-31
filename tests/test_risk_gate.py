import sys
import os
import unittest
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.trade import TradeProposal
from risk_management.risk_gate import RiskGate, RiskGateException

class TestRiskGate(unittest.TestCase):
    def setUp(self):
        self.gate = RiskGate(max_quantity=5)

    def test_valid_bull_put_spread(self):
        """Test: Bull Put Spread valid harusnya lolos Risk Gate."""
        proposal = TradeProposal(
            symbol="SPY",
            expiry=date(2024, 8, 30),
            short_strike=500.0,
            long_strike=495.0, # Valid: short > long
            quantity=1,
            conviction_score=80,
            reasoning="Test valid"
        )
        self.assertTrue(self.gate.validate_proposal(proposal))

    def test_invalid_spread_inverted(self):
        """Test: Strike terbalik (short < long) harusnya diblokir."""
        proposal = TradeProposal(
            symbol="SPY",
            expiry=date(2024, 8, 30),
            short_strike=490.0,
            long_strike=495.0, # Invalid: short < long
            quantity=1,
            conviction_score=80,
            reasoning="Test invalid"
        )
        with self.assertRaises(RiskGateException) as context:
            self.gate.validate_proposal(proposal)
        self.assertTrue("harus lebih besar dari Long strike" in str(context.exception))

    def test_invalid_spread_same_strike(self):
        """Test: Strike sama (short == long) harusnya diblokir."""
        proposal = TradeProposal(
            symbol="SPY",
            expiry=date(2024, 8, 30),
            short_strike=500.0,
            long_strike=500.0, # Invalid: short == long
            quantity=1,
            conviction_score=80,
            reasoning="Test invalid"
        )
        with self.assertRaises(RiskGateException) as context:
            self.gate.validate_proposal(proposal)
        self.assertTrue("harus lebih besar dari Long strike" in str(context.exception))

    def test_quantity_exceeds_max(self):
        """Test: Quantity melebihi batas maksimal harusnya diblokir."""
        proposal = TradeProposal(
            symbol="SPY",
            expiry=date(2024, 8, 30),
            short_strike=500.0,
            long_strike=495.0,
            quantity=10, # Melebihi max_quantity=5
            conviction_score=80,
            reasoning="Test qty max"
        )
        with self.assertRaises(RiskGateException) as context:
            self.gate.validate_proposal(proposal)
        self.assertTrue("melebihi batas maksimal" in str(context.exception))

    def test_risk_limit_exceeded(self):
        """Test: Maksimal risk nominal per trade melebihi $1000 harusnya diblokir."""
        proposal = TradeProposal(
            symbol="SPY",
            expiry=date(2024, 8, 30),
            short_strike=500.0,
            long_strike=485.0, # Spread width = $15 => $1500 risk per kontrak
            quantity=1,
            conviction_score=80,
            reasoning="Test max risk"
        )
        with self.assertRaises(RiskGateException) as context:
            self.gate.validate_proposal(proposal)
        self.assertTrue("terlalu besar untuk satu trade" in str(context.exception))

if __name__ == "__main__":
    unittest.main()
