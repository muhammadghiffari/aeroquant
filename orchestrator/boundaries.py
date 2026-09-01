"""
orchestrator/boundaries.py — Replaceable strategy boundaries.

PRD §5.4: CandidateBuilder, TradingMandate, and ExitPolicy are replaceable
black-box boundaries. The strategy logic behind them stays owned by §3/§8 of
the PRD, not by this file.

These stubs provide the smallest interface needed for graph wiring and tests.
Each stub is designed to be replaced with a real implementation without
touching the graph nodes themselves.

MCP COMPLIANCE NOTE (PRD §12 open item):
  The EvidenceGateway pattern is not yet confirmed as satisfying the
  "must use Alpaca MCP server or CLI" hard eligibility gate. The MCP
  compliance interpretation remains unresolved (PRD §5.4 / §12).
  Any real EvidenceGatherer implementation should maintain this boundary
  explicitly rather than silently claiming the issue is resolved.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.state import CycleState, ValidatedCandidate

logger = logging.getLogger("aeroquant.boundaries")


# ---------------------------------------------------------------------------
# TradingMandate — the current market/trading policy envelope
# ---------------------------------------------------------------------------


class TradingMandate(ABC):
    """
    Abstract boundary: what is the market/trading policy right now?

    Implementations may read live Alpaca data, a config file, or a static policy.
    The graph receives this as context before proposing trades.
    """

    @abstractmethod
    def is_trading_allowed(self) -> tuple[bool, str]:
        """
        Returns (allowed, reason).
        If False, the cycle skips candidate generation.
        """
        ...

    @abstractmethod
    def allowed_sides(self) -> list[str]:
        """Returns list of allowed spread sides: ['iron_condor', 'bull_put_spread', ...]"""
        ...

    @abstractmethod
    def max_dte(self) -> int:
        """Maximum DTE for new entries."""
        ...

    @abstractmethod
    def iv_regime_required(self) -> bool:
        """True if IV Rank/Percentile must both be >= 60 to open new positions."""
        ...


class DefaultTradingMandate(TradingMandate):
    """Static policy matching PRD §8 defaults."""

    def is_trading_allowed(self) -> tuple[bool, str]:
        return True, "Market open, no halt active"

    def allowed_sides(self) -> list[str]:
        return ["iron_condor", "bull_put_spread", "bear_call_spread"]

    def max_dte(self) -> int:
        return 5

    def iv_regime_required(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# CandidateBuilder — generates candidate spread structures
# ---------------------------------------------------------------------------


class CandidateBuilder(ABC):
    """Build options-chain-backed candidates through the deterministic data plane.

    A returned candidate contains the exact ``TradeProposal`` to be evaluated by
    ``RiskManager`` plus the Alpaca options-chain snapshot provenance.  The Chief
    can select a candidate ID; it cannot construct or amend contract data.
    """

    @abstractmethod
    def build(
        self,
        *,
        state: "CycleState",
        mandate: TradingMandate,
    ) -> list["ValidatedCandidate"]:
        """Return validated candidates, or an empty list when the path is not ready.

        Implementations must obtain actual contracts, prices, and account-risk
        context from their data/account boundaries.  They must never fill missing
        values with synthetic legs or prices.
        """
        ...


class DefaultCandidateBuilder(CandidateBuilder):
    """Explicit no-op until deterministic contract selection is implemented.

    There is no current CandidateBuilder/contract-selection implementation in the
    repository.  Returning no candidates deliberately produces the graph's
    ``CANDIDATE_NOT_READY`` safe-rejection path.
    """

    def build(
        self,
        *,
        state: "CycleState",
        mandate: TradingMandate,
    ) -> list["ValidatedCandidate"]:
        logger.info(
            "DefaultCandidateBuilder is intentionally unimplemented; "
            "no deterministic candidates produced cycle=%s",
            state.cycle_id,
        )
        return []


# ---------------------------------------------------------------------------
# ExitPolicy — when and how to close existing positions
# ---------------------------------------------------------------------------


class ExitPolicy(ABC):
    """
    Abstract boundary: given a position's current state, should we close it?

    The ForceCloseGuard in the execution layer enforces PRD §9's mandatory
    force-close-before-expiry independently of this policy.
    """

    @abstractmethod
    def should_close(
        self,
        position: dict,
        current_credit: float,
        max_credit: float,
    ) -> tuple[bool, str]:
        """
        Returns (should_close, reason).
        reason is one of: 'tp', 'sl', 'manual', 'mandate_change', 'force_close'.
        """
        ...


class DefaultExitPolicy(ExitPolicy):
    """
    Stub: TP at 50% of max credit, SL at 125% of initial credit.

    Matches PRD §8 defaults.
    """

    def should_close(
        self,
        position: dict,
        current_credit: float,
        max_credit: float,
    ) -> tuple[bool, str]:
        if max_credit <= 0:
            return False, "no_credit"

        ratio = current_credit / max_credit
        if ratio >= 0.50:
            return True, "tp"
        if ratio <= -1.25:
            return True, "sl"
        return False, "no_signal"


# ---------------------------------------------------------------------------
# EvidenceGatherer — MCP/CLI boundary for market data
# ---------------------------------------------------------------------------


class EvidenceGatherer(ABC):
    """
    Abstract boundary: pull market evidence for the data engine.

    MCP COMPLIANCE: this boundary is intentionally explicit. The PRD marks
    the compliance interpretation as unresolved (PRD §5.4 / §12). Real
    implementations should wire to the Alpaca MCP server explicitly rather
    than silently assuming a read-only DataGatherer satisfies the eligibility
    gate.
    """

    @abstractmethod
    def gather(
        self,
        symbol: str,
        bundle_type: Literal["full", "snapshot"] = "snapshot",
    ) -> dict:
        """
        Returns a dict suitable for constructing a MarketDataBundle plus a
        timezone-aware ``snapshot_timestamp``. Missing, stale, or invalid market
        data is rejected by the graph; it is never replaced with synthetic evidence.
        """
        ...


from typing import Literal


class StubEvidenceGatherer(EvidenceGatherer):
    """Stub: returns no evidence. The graph will fail closed for entry cycles."""

    def gather(
        self,
        symbol: str,
        bundle_type: Literal["full", "snapshot"] = "snapshot",
    ) -> dict:
        logger.warning(
            "StubEvidenceGatherer: no live data wired. "
            "Evidence gathering will return empty — use fixtures in tests."
        )
        return {}


# ---------------------------------------------------------------------------
# SemanticMemory — FinMem/Reflexion boundary
# ---------------------------------------------------------------------------


class SemanticMemory(ABC):
    """
    Abstract boundary: semantic (long-term) memory retrieval.

    PRD §6: semantic memory holds generalized rules from Reflexion critique.
    PRD §5.4: embedding provider is unresolved — this boundary exists so
    the graph can receive memory context without depending on a specific
    embedding implementation.
    """

    @abstractmethod
    def retrieve(
        self,
        cycle_state: "CycleState",
        limit: int = 5,
    ) -> list[dict]:
        """
        Returns up to `limit` semantically relevant memory entries for the
        given cycle state (symbol, regime, etc.).
        Each entry is a dict with keys: rule, source_cycle, confidence.
        Returns empty list if nothing relevant is found.
        """
        ...


class StubSemanticMemory(SemanticMemory):
    """Stub: no-op. Semantic memory returns empty until embedding is wired."""

    def retrieve(
        self,
        cycle_state: "CycleState",
        limit: int = 5,
    ) -> list[dict]:
        return []


# ---------------------------------------------------------------------------
# CyclePersistence — storage boundary for cycle results
# ---------------------------------------------------------------------------


class CyclePersistence(ABC):
    """
    Abstract boundary: persist cycle state / results.

    PRD §5.4: SQLite WAL for competition, behind a repository boundary
    compatible with future Postgres swap.
    """

    @abstractmethod
    def save(self, cycle_state: "CycleState") -> tuple[bool, str]:
        """
        Persist the cycle state.
        Returns (success, error_message).
        account_id must be preserved in all records.
        """
        ...

    @abstractmethod
    def load(self, alpaca_account_id: str, cycle_id: str) -> "CycleState | None":
        """Load a specific cycle. Returns None if not found."""
        ...

    @abstractmethod
    def latest(self, alpaca_account_id: str) -> "CycleState | None":
        """Load the most recent completed cycle for this account."""
        ...


class StubCyclePersistence(CyclePersistence):
    """Stub: in-memory only. No database. Use for tests only."""

    def __init__(self) -> None:
        self._store: dict[str, "CycleState"] = {}

    def _key(self, alpaca_account_id: str, cycle_id: str) -> str:
        return f"{alpaca_account_id}:{cycle_id}"

    def save(self, cycle_state: "CycleState") -> tuple[bool, str]:
        self._store[self._key(cycle_state.alpaca_account_id, cycle_state.cycle_id)] = (
            cycle_state
        )
        return True, ""

    def load(self, alpaca_account_id: str, cycle_id: str) -> "CycleState | None":
        return self._store.get(self._key(alpaca_account_id, cycle_id))

    def latest(self, alpaca_account_id: str) -> "CycleState | None":
        matches = [
            s for key, s in self._store.items() if key.startswith(f"{alpaca_account_id}:")
        ]
        if not matches:
            return None
        return max(matches, key=lambda s: s.started_at)
