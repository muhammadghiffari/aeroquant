"""
execution/__init__.py — Execution plane.

This package contains deterministic execution services:
  - broker: Alpaca broker abstraction and concrete implementation
  - order_dispatcher: RiskDecision → broker order submission
  - force_close_guard: PRD §9 mandatory force-close guard
  - scheduler: polling force-close loop and broker state reconciliation

CRITICAL ARCHITECTURE RULE (CLAUDE.md / PRD §5.4):
  LLM agents may PROPOSE. Only the Risk Gate (agents/risk_manager.py) may AUTHORIZE.
  Only this execution plane may call Alpaca trading APIs. No LLM ever receives Alpaca trading tools.
"""

from execution.broker import AlpacaBroker
from execution.order_dispatcher import OrderDispatcher, DispatchResult
from execution.force_close_guard import (
    ForceCloseGuard,
    ForceCloseJob,
    ShortLegPosition,
    CloseOrder,
    PositionStatus,
    CLAIMED_BY_FORCE_CLOSE,
)
from execution.scheduler import (
    BrokerPositionTracker,
    BrokerReconcileResult,
)

__all__ = [
    "AlpacaBroker",
    "OrderDispatcher",
    "DispatchResult",
    "ForceCloseGuard",
    "ForceCloseJob",
    "ShortLegPosition",
    "CloseOrder",
    "PositionStatus",
    "CLAIMED_BY_FORCE_CLOSE",
    "BrokerPositionTracker",
    "BrokerReconcileResult",
]
