"""
agents — LLM-driven reasoning nodes.

NOTE: No agent in this package should ever receive Alpaca trading tools.
All trading authorization flows through the deterministic Risk Gate in risk_manager.py.
LLM agents may PROPOSE; only the Risk Gate may AUTHORIZE.
"""

from agents.risk_manager import (
    RiskDecision,
    OrderIntent,
    RiskStatus,
    RiskManager,
    TradeSide,
    SCHEMA_VERSION as RISK_SCHEMA_VERSION,
)

__all__ = [
    "RiskDecision",
    "OrderIntent",
    "RiskStatus",
    "RiskManager",
    "TradeSide",
]
