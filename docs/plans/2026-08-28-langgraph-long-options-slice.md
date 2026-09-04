# LangGraph Long Options Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a five-symbol, long-option-only paper strategy through a checkpointed LangGraph decision path with conservative exits and SQLite audit state.

**Architecture:** The existing quantitative and agent components remain reusable nodes. LangGraph coordinates the symbol decision flow, while deterministic Python owns risk checks, exit decisions, order submission, and broker reconciliation. SQLite records cycles and order intents; Alpaca remains the source of truth for fills and positions.

**Tech Stack:** Python 3.10, LangGraph, SQLite WAL, Ollama Qwen3.5:9B, Alpaca paper Trading API, pytest.

---

### Task 1: Fixed Trading Mandate

**Files:**
- Modify: `config.py`
- Test: `tests/test_config.py`

- [ ] Add the fixed watchlist `SPY`, `QQQ`, `AAPL`, `NVDA`, `MSFT`, Qwen3.5:9B default, final-close date, and long-option exit policy settings.
- [ ] Test that configuration exposes exactly five unique symbols and a paper-only LLM model setting.

### Task 2: Deterministic Exit Policy

**Files:**
- Create: `execution/exit_policy.py`
- Modify: `execution/position_manager.py`
- Test: `tests/test_exit_policy.py`

- [ ] Write failing tests for executable-profit close, 50% loss stop, DTE force-close, and final-deadline close.
- [ ] Evaluate long-option exits using the bid price for a sell-to-close and return a reason without submitting an order.
- [ ] Delegate only an approved close reason to the executor.

### Task 3: LangGraph Decision Coordinator

**Files:**
- Create: `orchestrator/langgraph_cycle.py`
- Modify: `orchestrator/pipeline.py`
- Modify: `requirements.txt`
- Test: `tests/test_langgraph_cycle.py`

- [ ] Write a failing test proving the graph returns a `WAIT` action when the graph is given a completed no-trade state.
- [ ] Implement typed state and graph nodes for quant, analysis, chief, risk, and execution handoff.
- [ ] Route existing `run_cycle` symbol processing through the compiled graph without giving LLM nodes broker-write access.

### Task 4: SQLite Operational Audit Foundation

**Files:**
- Create: `execution/operational_store.py`
- Modify: `orchestrator/pipeline.py`
- Test: `tests/test_operational_store.py`

- [ ] Write failing tests for idempotent persisted cycle records and pre-submit order intents.
- [ ] Enable SQLite WAL and persist a cycle record plus durable order intent before submission.
- [ ] Record broker acknowledgement after the submit response; leave broker fill confirmation to reconciliation.

### Task 5: Verification and Paper-Market Forward Test

**Files:**
- Modify: `docs/PRD.md`

- [ ] Run the complete test suite and compile check.
- [ ] Verify the Alpaca paper account is active, not trading-blocked, and market-open.
- [ ] Run a five-symbol dry-run followed by one approved paper-market entry only if all checks pass.
- [ ] Reconcile broker status and record the observed order/position outcome.
