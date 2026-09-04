# Option-Profit Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make option-contract net profitability the primary calibrated confidence with a `0.60` lower-bound entry gate and shadow learning.

**Architecture:** Keep the existing underlying confidence as a direction and regime feature. Add contract microstructure/profit metrics, durable shadow observations in SQLite, and a causal outcome resolver. The pipeline exposes `GREEN`, `AMBER`, and `WAIT_DATA`; only `GREEN` reaches the existing whitelist, risk, and paper executor.

**Tech Stack:** Python 3.10+, NumPy, pandas, SQLite, alpaca-py, pytest.

**Status:** Core implementation and paper-only rollout are complete. Calibration
remains shadow-only until enough point-in-time option outcomes exist.

---

## File Map

- Create: `quant_engine/contract_profitability.py` for contract features and conservative P&L.
- Create: `quant_engine/contract_confidence.py` for matched outcome lower bounds.
- Create: `execution/shadow_store.py` for SQLite observations and resolution.
- Modify: `data_engine/option_data.py` for activity/OI metadata and shadow candidates.
- Modify: `quant_engine/engine.py` for contract metrics and final confidence.
- Modify: `orchestrator/pipeline.py` for non-dry-run shadow observe/resolve.
- Modify: `config.py` for threshold `0.60` and contract settings.
- Test: `tests/test_contract_profitability.py`, `tests/test_contract_confidence.py`, `tests/test_shadow_store.py`, and existing integration tests.
- Create: `scripts/audit_contract_confidence.py` for read-only shadow/outcome diagnostics.
- Create: `docs/specs/2026-09-02-option-profit-confidence-design.md`.

## Tasks

### Task 1: Configuration and contract feature tests

- [x] Write failing tests for threshold `0.60`, conservative ask-to-bid P&L,
  theta decay, quote freshness, activity metadata, and invalid data.
- [x] Implement configuration and pure contract feature functions.
- [x] Run focused tests.

### Task 2: Durable shadow observations

- [x] Write failing tests for idempotent observation insert, horizon resolution,
  net-P&L labels, and unresolved rows.
- [x] Add SQLite table/functions without changing broker state.
- [x] Run focused tests and existing operational-store tests.

### Task 3: Calibrated contract confidence

- [x] Write failing tests for feature buckets, minimum 30 samples, Wilson lower
  bound, `GREEN`/`AMBER`/`WAIT_DATA`, and causal resolution.
- [x] Implement matched outcome estimator.
- [x] Run focused tests.

### Task 4: Pipeline integration

- [x] Write failing tests proving AMBER records shadow data but cannot call
  execution, while GREEN preserves existing whitelist/risk/executor gates.
- [x] Integrate contract confidence and shadow resolution after Quant.
- [x] Keep LLM advisory only after all deterministic gates pass.
- [x] Run the full test suite.

### Task 5: Runtime audit and rollout

- [x] Add read-only audit script and report fields for underlying versus contract
  confidence, sample counts, P&L, and rejection reasons.
- [x] Run compile, focused tests, full tests, dry-run, and paper reconciliation.
- [x] Restart only the current entry task after verifying one worker and monitor.
- [x] Confirm only `GREEN` or explicitly labelled `GREEN_PROXY` can reach paper
  risk checks; no live trading is enabled.

## Remaining Calibration Work

- Cumulative daily option volume and open interest are not present in Alpaca
  chain snapshots; current activity fields are quote size and last-trade size.
- Historical point-in-time option quotes are still required before resolved
  shadow outcomes can be treated as a calibrated probability.
- The previous mixed store was archived into the new calibration version before
  the one-hour rollout; the active store starts clean.

## Hourly Execution Extension

### Task 6: Multi-timeframe bars and hourly shadow resolution

**Files:**
- Modify: `data_engine/stock_data.py` with a historical `1Hour` OHLCV helper.
- Modify: `quant_engine/engine.py` to use daily context plus hourly entry analysis.
- Modify: `execution/shadow_store.py` with a `timeframe` column and isolated `1H` resolution.
- Modify: `orchestrator/pipeline.py` to retain shadow-only analysis results.
- Modify: `server.py` to expose historical chart bars.
- Test: stock-data, shadow-store, quant-engine, and pipeline integration tests.

- [x] Add a failing test proving Alpaca hourly bars normalize to ascending OHLCV rows.
- [x] Add a failing test proving daily and hourly shadow observations cannot resolve against each other.
- [x] Implement hourly fetching with explicit `TimeFrame.Hour` and `DataFeed.IEX`.
- [x] Implement timeframe-aware shadow persistence and one-hour resolution.
- [x] Keep daily context, use hourly completed bars for entry features, and expose both in reports.
- [x] Add `SHADOW_ONLY` analysis output without allowing executor calls.
- [x] Add a historical-bar dashboard endpoint for visual trend consumers.
- [x] Run focused tests, full tests, compile, paper dry-run, and broker reconciliation.

### Task 7: Historical stock entry proxy

- [x] Use real completed `1H` stock bars to calculate causal one-hour setup
  confidence without fabricating option outcomes.
- [x] Keep option-profit confidence separate and visibly marked as unavailable
  until real option outcomes exist.
- [x] Keep LLM trend, volatility, news, manager, chief, and risk calls active in
  both the proxy and shadow-only lanes.
- [x] Expose `GREEN_PROXY`, `SHADOW_ONLY`, source, horizon, and calibration
  limitation in reports and dashboard copy.
