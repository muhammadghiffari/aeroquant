"""
orchestrator/graph.py — LangGraph construction and compilation.

PRD §5.4 graph structure:
  precheck -> evidence -> quant -> candidates -> memory
    -> [volatility || macro]   (parallel fan-out)
    -> technical
    -> [bull || bear]          (parallel fan-out)
    -> chief
    -> validator
    -> risk_gate
    -> persist

Key design decisions:
  - thread_id = f"{alpaca_account_id}:{cycle_id}" for account-scoped checkpointing.
  - Bounded chief ↔ validator repair loop: max 3 hops.
  - Production compilation uses the official disk-backed ``SqliteSaver``;
    callers can still inject a checkpointer when they own its lifecycle.
  - Conditional edges for parallel fan-out using Send objects.
  - All LLM nodes use ModelGateway.generate() — never raw clients.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from orchestrator.state import CyclePhase, CycleState, MAX_REPAIR_HOPS

if TYPE_CHECKING:
    from orchestrator.boundaries import (
        CandidateBuilder,
        EvidenceGatherer,
        SemanticMemory,
        TradingMandate,
    )
    from data_engine.quant_engine import QuantEngine
    from agents.risk_manager import RiskManager
    from orchestrator.boundaries import CyclePersistence

logger = logging.getLogger("aeroquant.graph")


# Stable repository-local runtime storage for process-restart recovery.
DEFAULT_CHECKPOINT_DATABASE = (
    Path(__file__).resolve().parents[1] / "state" / "langgraph_checkpoints.db"
)


def create_sqlite_checkpointer(
    database_path: str | Path | None = None,
) -> SqliteSaver:
    """Create a durable official SQLite saver with strict deserialization."""
    path = (
        DEFAULT_CHECKPOINT_DATABASE
        if database_path is None
        else Path(database_path)
    ).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(
        connection,
        serde=JsonPlusSerializer(
            pickle_fallback=False,
            allowed_json_modules=None,
            allowed_msgpack_modules=None,
        ),
    )


def _state_delta(before: CycleState, after: CycleState) -> dict:
    """Return only fields written by a node for LangGraph reducer semantics.

    Node functions remain directly unit-testable as ``CycleState -> CycleState``.
    The compiled graph receives deltas so simultaneous branches do not each write
    a full stale state snapshot over one another.
    """
    return {
        name: getattr(after, name)
        for name in CycleState.model_fields
        if getattr(before, name) != getattr(after, name)
    }


def _node_update(node):
    """Adapt a typed node function to a partial LangGraph state update."""
    def wrapped(state: CycleState) -> dict:
        return _state_delta(state, node(state))

    return wrapped


# ---------------------------------------------------------------------------
# Parallel fan-out helpers
# ---------------------------------------------------------------------------

def _route_volatility_macro(state: CycleState) -> list[Send]:
    """
    Conditional edge: memory -> [volatility, macro] in parallel.

    Uses LangGraph's Send objects for true parallel fan-out.
    """
    return [
        Send(node="volatility", arg=state),
        Send(node="macro", arg=state),
    ]


def _route_bull_bear(state: CycleState) -> list[Send]:
    """
    Conditional edge: technical -> [bull, bear] in parallel.
    """
    return [
        Send(node="bull", arg=state),
        Send(node="bear", arg=state),
    ]


def _route_validator_decision(state: CycleState) -> str:
    """
    Routing after validator:
      - If valid: go to risk_gate
      - If invalid AND at max hops: go to persist (failed)
      - If invalid AND hops remaining: go back to chief (repair loop)
    """
    proposal_id = state.chief_report.proposal_id if state.chief_report else "unknown"
    if state.is_failure() or state.validator_report is None:
        return "persist"
    if state.validator_report.valid:
        return "risk_gate"

    # Invalid — a node marks the terminal failed state at the deterministic cap.
    if state.at_max_repair_hops:
        logger.warning(
            "validator_rejected_max_hops proposal=%s hops=%d",
            proposal_id,
            state.repair_hop,
        )
        return "persist"

    logger.info(
        "validator_repair_loop proposal=%s hop=%d",
        proposal_id,
        state.repair_hop,
    )
    return "chief"


def _route_chief_repair(state: CycleState) -> str:
    """
    After chief in the repair loop: go back to validator (not back to evidence/quant).
    This keeps repair focused on the proposal, not the full cycle.
    """
    return "validator"


def _route_risk_gate_decision(state: CycleState) -> str:
    """
    After risk_gate:
      - APPROVED -> persist (with execution handoff)
      - REJECTED -> persist (with rejection record)
    """
    return "persist"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(
    *,
    mandate,
    gatherer,
    builder,
    semantic_memory,
    quant_engine,
    risk_manager,
    persistence,
) -> StateGraph:
    """
    Build the AeroQuant LangGraph.

    Args:
        mandate: TradingMandate boundary
        gatherer: EvidenceGatherer boundary
        builder: CandidateBuilder boundary
        semantic_memory: SemanticMemory boundary
        quant_engine: QuantEngine instance (existing deterministic module)
        risk_manager: RiskManager instance (existing deterministic module)
        persistence: CyclePersistence boundary

    Returns:
        Uncompiled StateGraph (call .compile() on the result).
    """
    from orchestrator import nodes as n

    # --- Create the graph ---
    builder_graph = StateGraph(CycleState)

    # -------------------------------------------------------------------------
    # Nodes — deterministic + LLM
    # -------------------------------------------------------------------------

    # Deterministic nodes
    builder_graph.add_node(
        "precheck",
        _node_update(lambda state: n.node_precheck(state, mandate=mandate)),
    )
    builder_graph.add_node(
        "evidence",
        _node_update(lambda state: n.node_evidence(state, gatherer=gatherer)),
    )
    builder_graph.add_node(
        "quant",
        _node_update(lambda state: n.node_quant(state, engine=quant_engine)),
    )
    builder_graph.add_node(
        "candidates",
        _node_update(lambda state: n.node_candidates(state, builder=builder, mandate=mandate)),
    )
    builder_graph.add_node(
        "memory",
        _node_update(lambda state: n.node_memory(state, semantic_memory=semantic_memory)),
    )

    # LLM nodes
    builder_graph.add_node("volatility", _node_update(n.node_volatility))
    builder_graph.add_node("macro", _node_update(n.node_macro))
    builder_graph.add_node("technical", _node_update(n.node_technical))
    builder_graph.add_node("bull", _node_update(n.node_bull))
    builder_graph.add_node("bear", _node_update(n.node_bear))
    builder_graph.add_node("chief", _node_update(n.node_chief))

    # Validator + RiskGate
    builder_graph.add_node("validator", _node_update(n.node_validator))
    builder_graph.add_node(
        "risk_gate",
        _node_update(lambda state: n.node_risk_gate(state, risk_manager=risk_manager)),
    )

    # Persistence
    builder_graph.add_node(
        "persist",
        _node_update(lambda state: n.node_persist(state, persistence=persistence)),
    )

    # -------------------------------------------------------------------------
    # Edges
    # -------------------------------------------------------------------------

    # Linear sequence: precheck -> evidence -> quant -> candidates -> memory
    builder_graph.add_edge(START, "precheck")
    builder_graph.add_edge("precheck", "evidence")
    builder_graph.add_edge("evidence", "quant")
    builder_graph.add_edge("quant", "candidates")
    builder_graph.add_edge("candidates", "memory")

    # Parallel fan-out: memory -> [volatility, macro]
    builder_graph.add_conditional_edges(
        "memory",
        _route_volatility_macro,
    )

    # Merge parallel results into technical
    builder_graph.add_edge("volatility", "technical")
    builder_graph.add_edge("macro", "technical")

    # Parallel fan-out: technical -> [bull, bear]
    builder_graph.add_conditional_edges(
        "technical",
        _route_bull_bear,
    )

    # Merge into chief
    builder_graph.add_edge("bull", "chief")
    builder_graph.add_edge("bear", "chief")

    # Validator routing
    builder_graph.add_edge("chief", "validator")

    # Bounded repair loop: validator -> [risk_gate | chief | persist]
    builder_graph.add_conditional_edges(
        "validator",
        _route_validator_decision,
        {
            "risk_gate": "risk_gate",
            "chief": "chief",  # repair hop — chief re-arbitrates
            "persist": "persist",
        },
    )

    # After chief in repair loop: route back to validator (not re-running LLM branches)
    # This is handled by the chief node returning to validator via the conditional
    # The key insight: when validator says invalid, we go to chief, chief -> validator again
    # But we need to prevent re-running the full parallel branches.
    # Resolution: in repair mode, chief does NOT re-trigger the full graph.
    # The _route_validator_decision routing to "chief" already handles this:
    # chief -> validator (via the edge above) -> _route_validator_decision
    # This creates a validator<->chief loop bounded by MAX_REPAIR_HOPS.

    # RiskGate -> persist
    builder_graph.add_edge("risk_gate", "persist")

    # End
    builder_graph.add_edge("persist", END)

    return builder_graph


def compile_graph(
    *,
    mandate,
    gatherer,
    builder,
    semantic_memory,
    quant_engine,
    risk_manager,
    persistence,
    checkpointer=None,
    checkpoint_path: str | Path | None = None,
    interrupt_after: list[str] | None = None,
) -> "CompiledStateGraph":
    """
    Build and compile the AeroQuant graph.

    Args:
        checkpointer: an actual LangGraph checkpointer. When omitted, the
                      official durable ``SqliteSaver`` is created.
        checkpoint_path: optional path for the default ``SqliteSaver`` database.
                         It cannot be combined with an injected checkpointer.
        interrupt_after: optional LangGraph interrupt nodes, used by deterministic
                         crash/resume tests and never required for normal cycles.
    Returns:
        CompiledStateGraph ready for invocation.
    """
    graph = build_graph(
        mandate=mandate,
        gatherer=gatherer,
        builder=builder,
        semantic_memory=semantic_memory,
        quant_engine=quant_engine,
        risk_manager=risk_manager,
        persistence=persistence,
    )

    if checkpointer is not None and checkpoint_path is not None:
        raise ValueError("checkpoint_path cannot be used with an injected checkpointer")
    active_checkpointer = (
        create_sqlite_checkpointer(checkpoint_path)
        if checkpointer is None
        else checkpointer
    )

    compiled = graph.compile(
        checkpointer=active_checkpointer,
        interrupt_after=interrupt_after,
    )
    return compiled


def make_thread_config(
    alpaca_account_id: str,
    cycle_id: str,
) -> dict:
    """
    Build the LangGraph config dict for account-scoped checkpointing.

    thread_id = f"{alpaca_account_id}:{cycle_id}" per PRD §5.4.
    """
    if not alpaca_account_id or not cycle_id:
        raise ValueError("alpaca_account_id and cycle_id must both be non-empty")
    thread_id = f"{alpaca_account_id}:{cycle_id}"
    return {"configurable": {"thread_id": thread_id}}


# ---------------------------------------------------------------------------
# CompiledStateGraph type (for type hints)
# ---------------------------------------------------------------------------

try:
    from langgraph.graph.state import CompiledStateGraph
except ImportError:
    CompiledStateGraph = object  # type: ignore[misc, assignment]
