# Single-Leg Per-Ticker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict new options entries to one long call or long put per ticker while allowing several tickers in the portfolio.

**Architecture:** Validate the proposal type and one-leg direction at the deterministic risk boundary. Before risk approval, reject a ticker that already has an active lifecycle record. Reuse existing monitoring and long-option exit thresholds.

**Tech Stack:** Python 3.10, `alpaca-py`, pytest, FastAPI.

---

### Task 1: Restrict Proposal Structure

**Files:**
- Modify: `agents/risk_manager_agent.py`
- Test: `tests/test_risk.py`

- [ ] Write failing tests that reject a `BULL_PUT_SPREAD` and a `LONG_CALL` with a sell leg.
- [ ] Run `python -m pytest -q tests/test_risk.py` and confirm failure.
- [ ] Make `run_rule_checks()` reject strategy types other than `LONG_CALL`, `LONG_PUT`, and `WAIT`.
- [ ] Run `python -m pytest -q tests/test_risk.py` and confirm pass.

### Task 2: Block Duplicate Ticker Entries

**Files:**
- Modify: `orchestrator/pipeline.py`
- Test: `tests/test_local_run.py`

- [ ] Write a failing test for an active record of the same ticker.
- [ ] Run the focused test and confirm failure.
- [ ] Return a declared `SKIPPED_ACTIVE_POSITION` result before agent/data work for that ticker.
- [ ] Run the focused test and confirm pass.

### Task 3: Verify Safe Market Flow

**Files:**
- Modify: `docs/specs/2026-08-27-single-leg-per-ticker-design.md`
- Test: full suite and one `--dry-run` cycle

- [ ] Run `python -m pytest -q`.
- [ ] Run `python main.py --once --symbol SPY --dry-run`.
- [ ] Verify the report has no broker execution and any proposal is a long call, long put, or wait.
