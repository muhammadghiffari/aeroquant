"""
execution/__init__.py — Execution plane.

This package contains deterministic execution services:
  - force_close_guard: PRD §9 mandatory force-close-before-expiry guard
  - order_dispatcher: Converts OrderIntent → Alpaca API calls (trading tools live here)

CRITICAL ARCHITECTURE RULE (CLAUDE.md / PRD §5.4):
  - LLM agents may PROPOSE.
  - Only the Risk Gate (agents/risk_manager.py) may AUTHORIZE.
  - Only this execution layer may call Alpaca trading APIs.
  - No LLM ever receives Alpaca trading tools.
"""

from execution.force_close_guard import (
    ForceCloseGuard,
    ForceCloseJob,
    ShortLegPosition,
    CloseOrder,
    CLAIMED_BY_FORCE_CLOSE,
)

__all__ = [
    "ForceCloseGuard",
    "ForceCloseJob",
    "ShortLegPosition",
    "CloseOrder",
    "CLAIMED_BY_FORCE_CLOSE",
]
