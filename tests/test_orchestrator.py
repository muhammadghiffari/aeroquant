"""Observable §12 LangGraph wiring tests (offline and deterministic)."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from agents.risk_manager import LegSpec, RiskManager, RiskStatus, SpreadSpec, TradeProposal, TradeSide
from data_engine.quant_engine import DataQuality, QuantEngine
from orchestrator import (
    CandidateProvenance,
    CandidateReadiness,
    CyclePhase,
    CycleState,
    DefaultCandidateBuilder,
    DefaultTradingMandate,
    StubCyclePersistence,
    StubSemanticMemory,
    ValidatedCandidate,
    build_graph,
    compile_graph,
    make_thread_config,
)
from orchestrator.boundaries import CandidateBuilder, EvidenceGatherer, TradingMandate
from orchestrator.state import (
    BullBearReport,
    ChiefReport,
    MacroReport,
    TechnicalReport,
    ValidatorReport,
    VolatilityReport,
)
from orchestrator import nodes as node_module


class FixtureEvidenceGatherer(EvidenceGatherer):
    """Recorded-shaped signal fixture; it is not a contract-selection fixture."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload if payload is not None else market_evidence()
        self.calls = 0

    def gather(self, symbol: str, bundle_type: str = "snapshot") -> dict:
        self.calls += 1
        return self.payload


class CountingMandate(DefaultTradingMandate):
    def __init__(self) -> None:
        self.calls = 0

    def is_trading_allowed(self) -> tuple[bool, str]:
        self.calls += 1
        return super().is_trading_allowed()


class FixtureCandidateBuilder(CandidateBuilder):
    def __init__(self, candidates: list[ValidatedCandidate]) -> None:
        self.candidates = candidates
        self.calls = 0

    def build(
        self,
        *,
        state: CycleState,
        mandate: TradingMandate,
    ) -> list[ValidatedCandidate]:
        self.calls += 1
        return list(self.candidates)


class RecordingRiskManager(RiskManager):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[TradeProposal] = []

    def evaluate(self, proposal: TradeProposal, **kwargs):
        self.seen.append(proposal)
        return super().evaluate(proposal, **kwargs)


class FakeGateway:
    """Records real node calls while supplying deterministic typed responses."""

    def __init__(self, chief_ids: list[str], *, synchronize_parallel: bool = False) -> None:
        self.chief_ids = deque(chief_ids)
        self.calls: list[tuple[str, str, str]] = []
        self._barriers = (
            {
                frozenset(("volatility_analyst", "macro_analyst")): threading.Barrier(2),
                frozenset(("bull_researcher", "bear_researcher")): threading.Barrier(2),
            }
            if synchronize_parallel
            else {}
        )
        self._arrivals: dict[frozenset[str], set[str]] = defaultdict(set)

    def generate(self, *, role, policy, messages, response_model, correlation_id):
        self.calls.append((role, policy, threading.current_thread().name))
        for pair, barrier in self._barriers.items():
            if role in pair:
                self._arrivals[pair].add(role)
                barrier.wait(timeout=3)

        responses = {
            "volatility_analyst": VolatilityReport(
                regime="high_iv", confidence=0.9, rationale="fixture", iv_regime_signal="high"
            ),
            "macro_analyst": MacroReport(sentiment="neutral", confidence=0.8),
            "technical_manager": TechnicalReport(
                trend="neutral", momentum_score=0.1, directional_conviction=0.5, summary="fixture"
            ),
            "bull_researcher": BullBearReport(verdict="proceed", score=0.7, arguments=["fixture"]),
            "bear_researcher": BullBearReport(verdict="modify", score=0.5, arguments=["fixture"]),
        }
        if role == "chief_strategy_agent":
            proposal_id = self.chief_ids.popleft()
            result = ChiefReport(
                proposal_id=proposal_id,
                side="bull_put_spread",
                rationale="Select a supplied fixture candidate only.",
                confidence=0.8,
            )
        else:
            result = responses[role]
        assert isinstance(result, response_model)
        return result, "deterministic-test-provider"


def market_evidence(*, snapshot_timestamp: datetime | None = None) -> dict:
    now = snapshot_timestamp or datetime.now(timezone.utc)
    index = pd.date_range(end=now, periods=260, freq="B", tz="UTC")
    steps = np.arange(len(index), dtype=float)
    closes = pd.Series(570.0 + (steps * 0.13) + np.sin(steps / 4.0), index=index)
    ivs = pd.Series(0.12 + (steps / (len(index) - 1) * 0.23), index=index)
    return {
        "symbol": "XSP",
        "spot_price": float(closes.iloc[-1]),
        "historical_closes": closes,
        "historical_iv": ivs,
        "atm_call_price": 4.1,
        "atm_put_price": 4.0,
        "iv_atm": float(ivs.iloc[-1]),
        "iv_25_delta_put": 0.38,
        "dte": 3,
        "open_interest": 1000,
        "iv_quality": DataQuality.PRIMARY,
        "spot_quality": DataQuality.PRIMARY,
        "chain_quality": DataQuality.PRIMARY,
        "snapshot_timestamp": now.isoformat(),
        "options_data_tier": "indicative",
        "news": [],
    }


def validated_candidate(*, proposal_id: str = "candidate-001") -> ValidatedCandidate:
    now = datetime.now(timezone.utc)
    expiration = now + timedelta(days=3)
    proposal = TradeProposal(
        proposal_id=proposal_id,
        timestamp=now,
        spread=SpreadSpec(
            legs=[
                LegSpec(
                    symbol="XSP260904P00600000",
                    action="SELL",
                    quantity=1,
                    strike=600.0,
                    expiration=expiration,
                    option_type="put",
                    bid_price=1.00,
                    ask_price=1.02,
                    mid_price=1.01,
                ),
                LegSpec(
                    symbol="XSP260904P00595000",
                    action="BUY",
                    quantity=1,
                    strike=595.0,
                    expiration=expiration,
                    option_type="put",
                    bid_price=0.10,
                    ask_price=0.102,
                    mid_price=0.101,
                ),
            ],
            dte=3,
            net_credit=0.909,
            max_credit=1.01,
            max_loss=499.091,
            side=TradeSide.SHORT_PUT_SPREAD,
        ),
        account_buying_power=100_000.0,
        account_equity=100_000.0,
        open_positions=0,
        current_exposure_pct=0.0,
        new_trade_exposure_pct=0.005,
        daily_realized_pnl_pct=0.0,
        week_realized_pnl_pct=0.0,
        iv_high_regime=True,
        momentum_zscore=0.0,
        consecutive_rejections=0,
    )
    return ValidatedCandidate(
        proposal=proposal,
        provenance=CandidateProvenance(
            source="alpaca_options_chain",
            data_tier="indicative",
            underlying_symbol="XSP",
            snapshot_timestamp=now,
            contract_snapshot_id="recorded-chain-snapshot-001",
        ),
    )


def as_state(result) -> CycleState:
    return result if isinstance(result, CycleState) else CycleState.model_validate(result)


def configure_gateway(monkeypatch, chief_ids: list[str], *, synchronize_parallel: bool = False) -> FakeGateway:
    gateway = FakeGateway(chief_ids, synchronize_parallel=synchronize_parallel)
    monkeypatch.setattr(node_module, "_MG", gateway)
    return gateway


def compiled_workflow(
    *,
    candidate_builder: CandidateBuilder | None = None,
    risk_manager: RiskManager | None = None,
    gatherer: EvidenceGatherer | None = None,
    mandate: TradingMandate | None = None,
    persistence: StubCyclePersistence | None = None,
    checkpointer=None,
    checkpoint_path=None,
    interrupt_after: list[str] | None = None,
):
    test_checkpointer = (
        MemorySaver()
        if checkpointer is None and checkpoint_path is None
        else checkpointer
    )
    return compile_graph(
        mandate=mandate or DefaultTradingMandate(),
        gatherer=gatherer or FixtureEvidenceGatherer(),
        builder=candidate_builder or DefaultCandidateBuilder(),
        semantic_memory=StubSemanticMemory(),
        quant_engine=QuantEngine(),
        risk_manager=risk_manager or RiskManager(),
        persistence=persistence or StubCyclePersistence(),
        checkpointer=test_checkpointer,
        checkpoint_path=checkpoint_path,
        interrupt_after=interrupt_after,
    )


class TestCycleState:
    def test_identity_is_required_and_account_scoped(self):
        with pytest.raises(ValidationError):
            CycleState(alpaca_account_id="", cycle_id="cycle")
        with pytest.raises(ValidationError):
            CycleState(alpaca_account_id="account", cycle_id="")
        state = CycleState(alpaca_account_id="account", cycle_id="cycle")
        assert state.to_thread_id() == "account:cycle"
        assert state.candidate_readiness == CandidateReadiness.NOT_READY

    def test_state_forbids_credentials_and_unexpected_fields(self):
        with pytest.raises(ValidationError):
            CycleState(alpaca_account_id="account", cycle_id="cycle", api_key="secret")
        with pytest.raises(ValidationError):
            CycleState(
                alpaca_account_id="account",
                cycle_id="cycle",
                evidence={"api_key": "secret"},
            )
        names = set(CycleState.model_fields)
        assert not {"api_key", "api_secret", "password", "credentials"} & names

    def test_repair_counter_is_bounded(self):
        state = CycleState(alpaca_account_id="account", cycle_id="cycle")
        for expected in range(1, 4):
            state = state.advance_repair_hop()
            assert state.repair_hop == expected
        assert state.advance_repair_hop().repair_hop == 3


class TestTopologyAndThreadIds:
    def test_exact_topology_and_real_checkpointer_property(self):
        saver = MemorySaver()
        compiled = compiled_workflow(checkpointer=saver)
        assert compiled.checkpointer is saver
        required = {
            "precheck", "evidence", "quant", "candidates", "memory", "volatility", "macro",
            "technical", "bull", "bear", "chief", "validator", "risk_gate", "persist",
        }
        assert required <= set(compiled.nodes)
        # StateGraph retains the concrete direct edges even where the compiled
        # drawable graph cannot render an unbounded Send branch.
        uncompiled = build_graph(
            mandate=DefaultTradingMandate(),
            gatherer=FixtureEvidenceGatherer(),
            builder=DefaultCandidateBuilder(),
            semantic_memory=StubSemanticMemory(),
            quant_engine=QuantEngine(),
            risk_manager=RiskManager(),
            persistence=StubCyclePersistence(),
        )
        assert {
            ("__start__", "precheck"),
            ("precheck", "evidence"),
            ("evidence", "quant"),
            ("quant", "candidates"),
            ("candidates", "memory"),
            ("volatility", "technical"),
            ("macro", "technical"),
            ("bull", "chief"),
            ("bear", "chief"),
            ("chief", "validator"),
            ("risk_gate", "persist"),
            ("persist", "__end__"),
        } <= uncompiled.edges
        assert {send.node for send in __import__("orchestrator.graph", fromlist=["_"])._route_volatility_macro(CycleState(alpaca_account_id="a", cycle_id="c"))} == {"volatility", "macro"}
        assert {send.node for send in __import__("orchestrator.graph", fromlist=["_"])._route_bull_bear(CycleState(alpaca_account_id="a", cycle_id="c"))} == {"bull", "bear"}

    def test_thread_id_is_canonical_and_isolated(self):
        same_a = make_thread_config("account-a", "cycle-1")
        same_b = make_thread_config("account-a", "cycle-1")
        other = make_thread_config("account-b", "cycle-1")
        assert same_a == same_b
        assert same_a["configurable"]["thread_id"] == "account-a:cycle-1"
        assert other["configurable"]["thread_id"] == "account-b:cycle-1"
        assert same_a != other
        with pytest.raises(ValueError):
            make_thread_config("", "cycle")


class TestCheckpointsAndResume:
    def test_memory_saver_persists_real_checkpoint_and_resumes_without_restart(self):
        saver = MemorySaver()
        mandate = CountingMandate()
        gatherer = FixtureEvidenceGatherer(payload={})  # Resume safely fails at evidence; no LLM needed.
        persistence = StubCyclePersistence()
        interrupted = compiled_workflow(
            mandate=mandate,
            gatherer=gatherer,
            persistence=persistence,
            checkpointer=saver,
            interrupt_after=["precheck"],
        )
        state = CycleState(alpaca_account_id="account-a", cycle_id="resume-001")
        config = make_thread_config(state.alpaca_account_id, state.cycle_id)

        interrupted.invoke(state.model_dump(), config)
        snapshot = interrupted.get_state(config)
        assert snapshot.values["phase"] == CyclePhase.PRECHECK
        assert snapshot.values["precheck_passed"] is True
        assert saver.get_tuple(config) is not None
        assert mandate.calls == 1

        # New compiled graph models a restarted worker; same real saver/thread resumes task state.
        resumed = compiled_workflow(
            mandate=mandate,
            gatherer=gatherer,
            persistence=persistence,
            checkpointer=saver,
        )
        final = as_state(resumed.invoke(None, config))
        assert mandate.calls == 1, "resume must not restart from __start__/precheck"
        assert gatherer.calls == 1
        assert final.failed_node == "evidence"
        assert final.persisted is True
        assert persistence.load("account-a", "resume-001") is not None
        assert saver.get_tuple(make_thread_config("account-b", "resume-001")) is None


class TestDurableSqliteCheckpoints:
    def test_sqlite_checkpoint_survives_new_graph_and_saver_instance(self, tmp_path):
        database_path = tmp_path / "langgraph-checkpoints.sqlite"
        state = CycleState(
            alpaca_account_id="account-durable",
            cycle_id="resume-from-disk",
        )
        config = make_thread_config(state.alpaca_account_id, state.cycle_id)
        mandate_a = CountingMandate()

        graph_a = compiled_workflow(
            mandate=mandate_a,
            checkpoint_path=database_path,
            interrupt_after=["quant"],
        )
        saver_a = graph_a.checkpointer
        assert isinstance(saver_a, SqliteSaver)
        assert isinstance(saver_a.serde, JsonPlusSerializer)
        assert saver_a.serde.pickle_fallback is False
        assert saver_a.serde._allowed_json_modules is None
        assert saver_a.serde._allowed_msgpack_modules is None

        graph_a.invoke(state.model_dump(), config)
        checkpointed_a = as_state(graph_a.get_state(config).values)
        assert checkpointed_a.phase == CyclePhase.QUANT
        assert checkpointed_a.quant_metrics is not None
        assert mandate_a.calls == 1

        # Instance A is closed and discarded before instance B touches the file.
        saver_a.conn.close()
        del graph_a

        with sqlite3.connect(database_path) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            checkpoint_count = connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (state.to_thread_id(),),
            ).fetchone()[0]
        assert journal_mode.lower() == "wal"
        assert checkpoint_count > 0

        mandate_b = CountingMandate()
        graph_b = compiled_workflow(
            mandate=mandate_b,
            checkpoint_path=database_path,
        )
        saver_b = graph_b.checkpointer
        assert isinstance(saver_b, SqliteSaver)
        assert saver_b is not saver_a

        recovered = as_state(graph_b.get_state(config).values)
        assert recovered.alpaca_account_id == "account-durable"
        assert recovered.cycle_id == "resume-from-disk"
        assert recovered.phase == CyclePhase.QUANT
        assert recovered.quant_metrics is not None
        assert mandate_b.calls == 0
        assert saver_b.get_tuple(config) is not None
        assert saver_b.get_tuple(
            make_thread_config("different-account", state.cycle_id)
        ) is None
        saver_b.conn.close()

    def test_sqlite_checkpoint_survives_separate_python_processes(self, tmp_path):
        database_path = tmp_path / "cross-process.sqlite"
        writer = textwrap.dedent(
            """
            import os
            import signal
            import sqlite3
            import sys
            from typing_extensions import TypedDict

            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.graph import END, START, StateGraph

            class State(TypedDict):
                marker: str

            graph_builder = StateGraph(State)
            graph_builder.add_node(
                "persist", lambda state: {"marker": state["marker"] + ":written"}
            )
            graph_builder.add_edge(START, "persist")
            graph_builder.add_edge("persist", END)
            connection = sqlite3.connect(sys.argv[1], check_same_thread=False)
            saver = SqliteSaver(
                connection,
                serde=JsonPlusSerializer(
                    pickle_fallback=False,
                    allowed_msgpack_modules=None,
                ),
            )
            graph = graph_builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "account-process:cycle-process"}}
            graph.invoke({"marker": "process-a"}, config)
            assert graph.get_state(config).values["marker"] == "process-a:written"
            os.kill(os.getpid(), signal.SIGKILL)
            """
        )
        reader = textwrap.dedent(
            """
            import sqlite3
            import sys
            from typing_extensions import TypedDict

            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.graph import END, START, StateGraph

            class State(TypedDict):
                marker: str

            graph_builder = StateGraph(State)
            graph_builder.add_node("persist", lambda state: state)
            graph_builder.add_edge(START, "persist")
            graph_builder.add_edge("persist", END)
            connection = sqlite3.connect(sys.argv[1], check_same_thread=False)
            saver = SqliteSaver(
                connection,
                serde=JsonPlusSerializer(
                    pickle_fallback=False,
                    allowed_msgpack_modules=None,
                ),
            )
            graph = graph_builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "account-process:cycle-process"}}
            snapshot = graph.get_state(config)
            assert saver.get_tuple(config) is not None
            assert snapshot.values["marker"] == "process-a:written"
            print("recovered-from-disk")
            connection.close()
            """
        )

        written = subprocess.run(
            [sys.executable, "-c", writer, str(database_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert written.returncode == -signal.SIGKILL, written.stderr
        recovered = subprocess.run(
            [sys.executable, "-c", reader, str(database_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert recovered.returncode == 0, recovered.stderr
        assert recovered.stdout.strip() == "recovered-from-disk"

class TestCandidateAndRiskBoundary:
    def test_default_candidate_builder_is_explicit_not_ready(self):
        state = CycleState(alpaca_account_id="account", cycle_id="cycle", evidence={"symbol": "XSP"})
        result = node_module.node_candidates(
            state,
            builder=DefaultCandidateBuilder(),
            mandate=DefaultTradingMandate(),
        )
        assert result.phase == CyclePhase.CANDIDATES
        assert result.candidate_readiness == CandidateReadiness.NOT_READY
        assert result.risk_proposal is None
        assert "not ready" in result.candidate_reason

    def test_bad_candidate_quote_is_rejected_before_risk_manager(self):
        candidate = validated_candidate()
        bad_leg = candidate.proposal.spread.legs[0].model_copy(update={"mid_price": 9.99})
        bad_proposal = candidate.proposal.model_copy(
            update={"spread": candidate.proposal.spread.model_copy(update={"legs": [bad_leg, *candidate.proposal.spread.legs[1:]]})}
        )
        bad = candidate.model_copy(update={"proposal": bad_proposal})
        state = CycleState(alpaca_account_id="account", cycle_id="cycle", evidence={"symbol": "XSP"})
        result = node_module.node_candidates(
            state,
            builder=FixtureCandidateBuilder([bad]),
            mandate=DefaultTradingMandate(),
        )
        assert result.phase == CyclePhase.FAILED
        assert result.failed_node == "candidates"
        assert "inconsistent bid/ask/mid" in result.error

    def test_fabricated_chief_stub_cannot_call_risk_manager_or_create_intent(self):
        state = CycleState(
            alpaca_account_id="account",
            cycle_id="cycle",
            chief_report=ChiefReport(
                proposal_id="XSP_STUB_PUT_SHORT",
                side="bull_put_spread",
                rationale="fabricated", confidence=1.0,
            ),
            validator_report=ValidatorReport(valid=True),
        )
        risk_manager = Mock(spec=RiskManager)
        result = node_module.node_risk_gate(state, risk_manager=risk_manager)
        assert result.risk_decision is not None
        assert result.risk_decision.decision == RiskStatus.REJECTED
        assert result.risk_decision.rule_names == ["CANDIDATE_NOT_READY"]
        assert result.risk_decision.order_intent is None
        risk_manager.evaluate.assert_not_called()

    def test_validated_candidate_is_the_exact_risk_manager_input_and_binds_account(self, monkeypatch):
        candidate = validated_candidate()
        gateway = configure_gateway(monkeypatch, [candidate.proposal_id])
        risk_manager = RecordingRiskManager()
        persistence = StubCyclePersistence()
        graph = compiled_workflow(
            candidate_builder=FixtureCandidateBuilder([candidate]),
            risk_manager=risk_manager,
            persistence=persistence,
        )
        config = make_thread_config("account-a", "candidate-001")
        final = as_state(graph.invoke(CycleState(alpaca_account_id="account-a", cycle_id="candidate-001").model_dump(), config))
        assert len(risk_manager.seen) == 1
        assert risk_manager.seen[0] == candidate.proposal
        assert final.risk_decision is not None
        assert final.risk_decision.decision == RiskStatus.APPROVED
        assert final.risk_decision.order_intent is not None
        assert final.risk_decision.order_intent.account_id == "account-a"
        assert all("STUB" not in leg.symbol for leg in risk_manager.seen[0].spread.legs)
        assert persistence.load("account-a", "candidate-001") == final
        roles = {role for role, _, _ in gateway.calls}
        assert {"volatility_analyst", "macro_analyst", "technical_manager", "bull_researcher", "bear_researcher", "chief_strategy_agent"} <= roles


class TestRepairLoopAndParallelism:
    def test_valid_first_attempt_reaches_risk_gate(self, monkeypatch):
        candidate = validated_candidate()
        gateway = configure_gateway(monkeypatch, [candidate.proposal_id])
        graph = compiled_workflow(candidate_builder=FixtureCandidateBuilder([candidate]))
        final = as_state(graph.invoke(
            CycleState(alpaca_account_id="account", cycle_id="first-valid").model_dump(),
            make_thread_config("account", "first-valid"),
        ))
        assert final.repair_hop == 0
        assert final.validator_report is not None and final.validator_report.valid
        assert final.risk_decision is not None and final.risk_decision.decision == RiskStatus.APPROVED
        assert [role for role, _, _ in gateway.calls].count("chief_strategy_agent") == 1

    def test_one_repair_then_success(self, monkeypatch):
        candidate = validated_candidate()
        gateway = configure_gateway(monkeypatch, ["not-a-candidate", candidate.proposal_id])
        graph = compiled_workflow(candidate_builder=FixtureCandidateBuilder([candidate]))
        final = as_state(graph.invoke(
            CycleState(alpaca_account_id="account", cycle_id="one-repair").model_dump(),
            make_thread_config("account", "one-repair"),
        ))
        assert final.repair_hop == 1
        assert final.validator_report is not None and final.validator_report.valid
        assert final.risk_decision is not None and final.risk_decision.decision == RiskStatus.APPROVED
        assert [role for role, _, _ in gateway.calls].count("chief_strategy_agent") == 2

    def test_repeated_invalid_selection_is_bounded_and_fails_closed(self, monkeypatch):
        candidate = validated_candidate()
        gateway = configure_gateway(monkeypatch, ["not-a-candidate"] * 4)
        graph = compiled_workflow(candidate_builder=FixtureCandidateBuilder([candidate]))
        final = as_state(graph.invoke(
            CycleState(alpaca_account_id="account", cycle_id="max-repairs").model_dump(),
            make_thread_config("account", "max-repairs"),
        ))
        assert final.repair_hop == 3
        assert final.phase == CyclePhase.FAILED
        assert final.failed_node == "validator"
        assert final.risk_decision is None
        assert final.persisted is True
        assert [role for role, _, _ in gateway.calls].count("chief_strategy_agent") == 4

    def test_parallel_branches_use_independent_gateway_calls(self, monkeypatch):
        candidate = validated_candidate()
        gateway = configure_gateway(
            monkeypatch, [candidate.proposal_id], synchronize_parallel=True
        )
        graph = compiled_workflow(candidate_builder=FixtureCandidateBuilder([candidate]))
        final = as_state(graph.invoke(
            CycleState(alpaca_account_id="account", cycle_id="parallel").model_dump(),
            make_thread_config("account", "parallel"),
        ))
        assert final.risk_decision is not None and final.risk_decision.decision == RiskStatus.APPROVED
        pairs = [
            {"volatility_analyst", "macro_analyst"},
            {"bull_researcher", "bear_researcher"},
        ]
        called = {role for role, _, _ in gateway.calls}
        for pair in pairs:
            assert pair <= called
        for pair, arrivals in gateway._arrivals.items():
            assert arrivals == set(pair)


class TestPersistenceAndFailures:
    def test_rejection_has_no_execution_intent_and_is_persisted_account_scoped(self, monkeypatch):
        configure_gateway(monkeypatch, ["unused"])
        repository = StubCyclePersistence()
        graph = compiled_workflow(persistence=repository)
        config = make_thread_config("account-a", "not-ready")
        final = as_state(graph.invoke(
            CycleState(alpaca_account_id="account-a", cycle_id="not-ready").model_dump(), config
        ))
        assert final.risk_decision is not None
        assert final.risk_decision.decision == RiskStatus.REJECTED
        assert final.risk_decision.rule_names == ["CANDIDATE_NOT_READY"]
        assert final.risk_decision.order_intent is None
        assert final.persisted is True
        assert repository.load("account-a", "not-ready") == final
        assert repository.load("account-b", "not-ready") is None

    def test_evidence_failure_is_not_converted_to_success(self):
        repository = StubCyclePersistence()
        graph = compiled_workflow(gatherer=FixtureEvidenceGatherer(payload={}), persistence=repository)
        final = as_state(graph.invoke(
            CycleState(alpaca_account_id="account", cycle_id="bad-evidence").model_dump(),
            make_thread_config("account", "bad-evidence"),
        ))
        assert final.phase == CyclePhase.FAILED
        assert final.failed_node == "evidence"
        assert final.error.startswith("Evidence error:")
        assert final.persisted is True
        assert repository.load("account", "bad-evidence") == final

    def test_stale_and_non_finite_evidence_fail_closed(self):
        state = CycleState(alpaca_account_id="account", cycle_id="data-quality")
        stale = market_evidence(snapshot_timestamp=datetime.now(timezone.utc) - timedelta(minutes=6))
        stale_result = node_module.node_evidence(state, gatherer=FixtureEvidenceGatherer(stale))
        assert stale_result.phase == CyclePhase.FAILED
        assert "stale" in stale_result.error

        invalid = market_evidence()
        invalid["spot_price"] = float("nan")
        invalid_result = node_module.node_evidence(state, gatherer=FixtureEvidenceGatherer(invalid))
        assert invalid_result.phase == CyclePhase.FAILED
        assert "spot_price" in invalid_result.error or "non-finite" in invalid_result.error
