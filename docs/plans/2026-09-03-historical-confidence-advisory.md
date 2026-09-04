# Historical Confidence Advisory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make historical confidence advisory rather than an entry veto while preserving current-market and risk safety gates.

**Architecture:** Keep historical OHLCV calculations and calibration fields in the quant report. Mark their confidence as advisory, construct candidates from valid current option quotes when direction is available, and let the existing strategy, whitelist, risk, intent, and broker gates decide execution.

**Tech Stack:** Python 3.10, pytest, JSON quant reports, Alpaca Trading API, scheduled PowerShell workers.

---

## File Map

- Modify `quant_engine/entry_confidence.py` to label historical confidence advisory.
- Modify `quant_engine/engine.py` to construct proxy candidates without using historical confidence as a veto.
- Modify `data_engine/option_data.py` to preserve live candidate filters while bypassing only the historical probability floor in advisory mode.
- Modify `agents/risk_manager_agent.py` to accept an explicitly advisory historical state after candidate validation.
- Modify `tests/test_momentum_quant.py` and `tests/test_contract_whitelist.py` with regression coverage.
- Add `docs/specs/2026-09-03-historical-confidence-advisory-design.md` as the approved behavior contract.

## Implementation

- [x] Add failing tests for low-confidence proxy candidates and advisory `WAIT_SEE` state.
- [x] Run focused tests and confirm failures are caused by the historical veto.
- [x] Add the advisory marker and bypass only the historical probability/state checks.
- [x] Keep quote freshness, bid/ask, spread, delta, DTE, whitelist, risk, and paper-only checks mandatory.
- [x] Run the full test suite and compile/dependency checks.
- [x] Stop workers, reset runtime through the broker guard, and run a fresh `SPY` canary.
- [x] Verify the canary and restart the full watchlist workers only after safe acceptance.

## Acceptance

- [x] Full suite passes with 236 tests.
- [x] Canary reports `entry_actionable=true` and `UNDERLYING_HISTORY_PROXY` despite low historical probability.
- [x] Canary remains safe when the news gate rejects; no paper order is submitted without valid news, strategy, and risk approval.
- [x] Momentum and Monitor scheduled tasks are running with one process each.
