# Autonomous Trading Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task.

**Goal:** Make the paper options bot autonomous across decision, asynchronous order lifecycle, recovery, risk blocking, and observability without allowing the LLM to bypass deterministic controls.

**Architecture:** Alpaca remains the broker source of truth. SQLite stores durable cycles and order intents; the JSON ledger remains a compatibility projection but only records a position after a broker order is filled. Pending entries and exits are reconciled before each decision cycle. The LLM proposes and explains; deterministic policy validates strategy, freshness, exposure, and lifecycle.

**Tech Stack:** Python 3.10, alpaca-py, LangGraph, SQLite WAL, pytest, Ollama structured JSON.

---

## File Map

- Modify `execution/operational_store.py`: durable order lifecycle and idempotent intent lookup.
- Modify `execution/position_manager.py`: pending-order reconciliation, broker fill pricing, recovery state.
- Modify `orchestrator/pipeline.py`: kill-switch gate, lifecycle-aware entry persistence, report events.
- Modify `agents/risk_manager_agent.py`: actual per-symbol exposure and execution-aware sizing.
- Modify `agents/strategy_decision_agent.py`: WAIT on conflicting signals unless a quantified exception exists.
- Modify `agents/base_agent.py`: strict schema validation and safe provider failure handling.
- Modify `main.py`: dry-run propagation to monitor mode.
- Modify `tests/*.py`: isolate runtime state and add lifecycle regression coverage.
- Modify `tests/smoke_agents.py`: use the current provider API.

## Task 1: Isolate Runtime State and Define Statuses

- [ ] Add failing tests proving tests cannot write the real ledger and that pending entry/exit statuses are distinct from filled positions.
- [ ] Run those tests and confirm expected failures.
- [ ] Add explicit internal statuses `INTENT_CREATED`, `SUBMITTED`, `FILLED`, `CLOSING`, `CLOSED`, `CANCELED`, `RECOVERY_REQUIRED`; persist broker order IDs and client IDs.
- [ ] Make test fixtures use temporary state paths and keep production reconciliation free of fake order IDs.
- [ ] Run focused lifecycle tests.

## Task 2: Make Order Lifecycle Crash-Safe

- [ ] Test duplicate-cycle behavior after a crash between submit and acknowledgement.
- [ ] Reuse an existing unresolved intent/client order ID and query Alpaca by client order ID before retrying.
- [ ] Create ledger positions only after confirmed fill; keep accepted/pending orders as intents.
- [ ] Confirm close orders by broker status before marking positions closed; retain `CLOSING` otherwise.
- [ ] Reconcile filled, canceled, expired, and rejected orders idempotently.

## Task 3: Enforce Risk Gates and Accurate P/L

- [ ] Test that kill switch prevents executor calls and that per-symbol exposure is enforced independently.
- [ ] Test sizing against adverse executable prices rather than midpoint.
- [ ] Use broker fill prices for entry basis and executable bid/ask for close P/L.
- [ ] Make `RECOVERY_REQUIRED` positions block entries and receive a deterministic recovery path.

## Task 4: Harden Autonomous Agent Decisions

- [ ] Test invalid enum, type, range, symbol, and leg outputs are rejected to WAIT.
- [ ] Add deterministic policy that blocks long premium when technical/volatility signals clash, unless the proposal includes a valid catalyst and risk justification.
- [ ] Ensure all fallback and sanity-review paths are fail-closed and usage is reported.
- [ ] Keep LangGraph as the cycle boundary and add explicit nodes for analysis, proposal, risk, and execution with a checkpoint-safe state payload.

## Task 5: Runtime Safety and Verification

- [ ] Test `--monitor --dry-run` never calls an order submitter.
- [ ] Repair the agent smoke test and run data, agent, and full integration checks.
- [ ] Add structured cycle reconciliation events to reports and dashboard state.
- [ ] Run `pytest -q`, `python -m compileall -q .`, read-only broker checks, and a dry-run for all five symbols.
- [ ] Do not run another real paper entry until all P0/P1 lifecycle tests pass.
