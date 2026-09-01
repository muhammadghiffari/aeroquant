"""
orchestrator — LangGraph workflow coordinator.

PRD §5.4: precheck -> evidence -> quant -> candidates -> memory
  -> [volatility || macro] -> technical -> [bull || bear]
  -> chief -> validator -> risk_gate -> persist

Components:
  state.py      — CycleState (LangGraph state schema)
  boundaries.py — Strategy boundaries (CandidateBuilder, TradingMandate, etc.)
  nodes.py      — All graph node implementations (deterministic + LLM)
  graph.py      — Graph construction and compilation

Safety rules enforced at the graph level:
  - No LLM node receives Alpaca trading tools.
  - RiskManager.evaluate() is the only path from proposal to execution.
  - chief ↔ validator repair loop is bounded (MAX_REPAIR_HOPS = 3).
  - thread_id is always f"{alpaca_account_id}:{cycle_id}".
"""

from orchestrator.state import (
    CandidateProvenance,
    CandidateReadiness,
    CyclePhase,
    CycleState,
    ValidatedCandidate,
    MAX_REPAIR_HOPS,
    SCHEMA_VERSION,
    BullBearReport,
    ChiefReport,
    MacroReport,
    TechnicalReport,
    ValidatorReport,
    VolatilityReport,
)

from orchestrator.boundaries import (
    CandidateBuilder,
    CyclePersistence,
    DefaultCandidateBuilder,
    DefaultExitPolicy,
    DefaultTradingMandate,
    EvidenceGatherer,
    SemanticMemory,
    StubCyclePersistence,
    StubEvidenceGatherer,
    StubSemanticMemory,
    TradingMandate,
    ExitPolicy,
)

from orchestrator.candidate_builder import XSPCandidateBuilder

from orchestrator.graph import (
    DEFAULT_CHECKPOINT_DATABASE,
    build_graph,
    compile_graph,
    create_sqlite_checkpointer,
    make_thread_config,
    CompiledStateGraph,
)

__all__ = [
    # State
    "CycleState",
    "CyclePhase",
    "CandidateReadiness",
    "CandidateProvenance",
    "ValidatedCandidate",
    "MAX_REPAIR_HOPS",
    "SCHEMA_VERSION",
    "BullBearReport",
    "ChiefReport",
    "MacroReport",
    "TechnicalReport",
    "ValidatorReport",
    "VolatilityReport",
    # Boundaries
    "CandidateBuilder",
    "TradingMandate",
    "ExitPolicy",
    "SemanticMemory",
    "EvidenceGatherer",
    "CyclePersistence",
    "DefaultCandidateBuilder",
    "DefaultTradingMandate",
    "DefaultExitPolicy",
    "StubEvidenceGatherer",
    "StubSemanticMemory",
    "StubCyclePersistence",
    "XSPCandidateBuilder",
    # Graph
    "DEFAULT_CHECKPOINT_DATABASE",
    "build_graph",
    "compile_graph",
    "create_sqlite_checkpointer",
    "make_thread_config",
    "CompiledStateGraph",
]
