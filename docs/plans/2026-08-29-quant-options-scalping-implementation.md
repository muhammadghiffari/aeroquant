# Quant-First Options Scalping Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD and review each checkpoint before continuing.

**Goal:** Add a paper-only intraday options scalping mode where deterministic quant signals choose call/put direction and a bounded LLM supervisor can only adjust validated price levels.

> **Archived:** This plan was superseded by
> `docs/plans/2026-09-01-deterministic-single-leg-momentum.md`. The scalp mode
> described here is not executable.

**Architecture:** Keep the existing swing/analysis pipeline intact and add a focused scalping pipeline. Intraday data feeds pure indicator functions, the signal engine selects direction and a liquid option, the supervisor validates bounded entry/TP/SL overrides, and the broker position manager remains the source of truth.

**Tech Stack:** Python 3.10, pandas, numpy, alpaca-py, pytest, existing JSON ledger and SQLite operational store.

---

## File Map

- Create `quant_engine/scalping.py`: pure intraday indicators, direction signal, contract selection, and baseline levels.
- Create `agents/scalp_supervisor.py`: optional one-call LLM review with strict output bounds.
- Create `orchestrator/scalping_pipeline.py`: fast paper-only cycle and report persistence.
- Modify `data_engine/stock_data.py`: fetch recent minute bars.
- Modify `data_engine/option_data.py`: expose a deterministic scalp contract selector.
- Modify `execution/executor.py`: accept a validated single-leg entry price.
- Modify `execution/ledger.py`: persist scalp levels and time-stop metadata.
- Modify `execution/exit_policy.py`: honor per-position levels and time stops.
- Modify `execution/position_manager.py`: reconcile pending entries before broker position comparison.
- Modify `main.py`: add `--scalp-once` and `--scalp-loop` modes.
- Modify `config.py`: add conservative scalp thresholds and supervisor bounds.
- Create `tests/test_scalping_quant.py`: unit tests for indicators, directional symmetry, and WAIT behavior.
- Create `tests/test_scalp_supervisor.py`: bounds and rationale tests.
- Create `tests/test_scalping_pipeline.py`: dry-run, no-trade, and forced call/put integration tests.

### Task 1: Quant Signal Tests

**Files:** Create `tests/test_scalping_quant.py`; create `quant_engine/scalping.py` skeleton.

- [ ] Write failing tests for bullish, bearish, and conflicting 1-minute/5-minute features.
- [ ] Write failing tests that require no signal for stale/short/zero-volume data.
- [ ] Write failing tests for option candidate direction, DTE, delta, and spread filters.
- [ ] Run `pytest tests/test_scalping_quant.py -v` and confirm failures are missing behavior, not test errors.

### Task 2: Intraday Data And Pure Quant Engine

**Files:** Modify `data_engine/stock_data.py`; create `quant_engine/scalping.py`; modify `config.py`.

- [ ] Add a recent minute-bar fetch using `StockBarsRequest` and `TimeFrame.Minute`, returning sorted OHLCV data.
- [ ] Implement EMA, RSI, ATR, session VWAP, momentum, and relative-volume helpers without network calls.
- [ ] Build a primary 5-minute feature frame and a 1-minute confirmation frame from fixture data.
- [ ] Return `BULLISH`, `BEARISH`, or `WAIT` with numeric features and explicit rejection reasons.
- [ ] Require EMA/VWAP/momentum agreement, RSI safety range, volume threshold, and minimum bar count.
- [ ] Add conservative scalp settings: 7-21 DTE, delta 0.45-0.70, max spread 5%, 0.5% trade risk, 1.5% daily loss, and bounded level overrides.
- [ ] Run the Task 1 tests and confirm they pass.

### Task 3: Deterministic Contract And Level Selection

**Files:** Modify `data_engine/option_data.py`; extend `quant_engine/scalping.py`; extend `tests/test_scalping_quant.py`.

- [ ] Select calls only for bullish signals and puts only for bearish signals.
- [ ] Filter by configured DTE, delta, positive bid/ask, quote freshness when available, and spread percentage.
- [ ] Score candidates by delta closeness, spread, and distance from the desired expiry; never let an LLM introduce a symbol.
- [ ] Compute baseline entry from ask, take-profit from option premium, stop-loss from option premium, and a time-stop timestamp.
- [ ] Reject a candidate if its cost exceeds the scalp risk budget or if its breakeven move exceeds the expected intraday move.
- [ ] Run the focused quant tests again.

### Task 4: Constrained LLM Supervisor

**Files:** Create `agents/scalp_supervisor.py`; create `tests/test_scalp_supervisor.py`.

- [ ] Write failing tests for accepted bounded adjustments, rejected out-of-bound adjustments, missing rationale, direction changes, and unknown contracts.
- [ ] Implement a JSON supervisor schema containing only approve/veto, entry, take-profit, stop-loss, and rationale.
- [ ] Validate every adjustment against quant baseline: entry +/-3%, TP/SL +/-10%, positive prices, and stop/target ordering.
- [ ] Require rationale to reference supplied quant features; otherwise retain the quant baseline.
- [ ] Treat provider errors or malformed output as a veto/fallback to quant levels, never as permission to trade.
- [ ] Run the supervisor tests and confirm they pass.

### Task 5: Fast Scalping Pipeline

**Files:** Create `orchestrator/scalping_pipeline.py`; modify `execution/executor.py`, `execution/ledger.py`, `execution/exit_policy.py`; create `tests/test_scalping_pipeline.py`.

- [ ] Write failing dry-run tests proving WAIT creates no LLM call, no intent, no order, and no ledger mutation.
- [ ] Write failing bullish and bearish tests proving the selected order is respectively a call and a put.
- [ ] Write failing tests proving supervisor levels are passed only after bounds validation.
- [ ] Implement broker/account snapshot, ledger reconciliation, intraday quant signal, candidate selection, supervisor review, risk gate, and report persistence in that order.
- [ ] Keep all money values rounded to cents at the broker boundary and persist raw broker values in the execution record.
- [ ] Extend single-leg execution to use the validated entry limit price while preserving paper-only assertions and client order IDs.
- [ ] Persist `entry_price`, `take_profit_price`, `stop_loss_price`, `time_stop_at`, `direction`, and quant feature summary on each scalp position.
- [ ] Update exit policy to use conservative bid-side price, per-position TP/SL, underlying invalidation, and time stop.
- [ ] Run focused pipeline tests and fix only production code when failures identify missing behavior.

### Task 6: Reconciliation And CLI

**Files:** Modify `execution/position_manager.py`, `main.py`, `config.py`; extend `tests/test_scalping_pipeline.py`.

- [ ] Reconcile pending entries against broker status before comparing tracked positions to broker positions.
- [ ] Ensure a manually closed broker position is marked closed before any new scalp entry is considered.
- [ ] Add `--scalp-once` and `--scalp-loop`; default interval is 1 minute and the loop catches cycle failures without submitting duplicates.
- [ ] Keep old `--once`, `--loop`, and `--monitor` behavior available.
- [ ] Add a process lock or single-run guard for scalp mode so two loops cannot enter the same ticker concurrently.
- [ ] Add explicit report fields for quant signal, supervisor decision, applied override, and rejection reason.

### Task 7: Verification And Paper Rollout Gate

**Files:** All changed files; `docs/specs/2026-08-29-quant-options-scalping-design.md`.

- [ ] Run `pytest -q` and require zero failures.
- [ ] Run `python -m compileall agents data_engine execution orchestrator quant_engine tests`.
- [ ] Run fixture-based dry runs containing both bullish and bearish signals and verify both LONG_CALL and LONG_PUT paths.
- [ ] Query Alpaca paper positions and orders; reconcile the manually closed ledger before any non-dry-run command.
- [ ] Run one paper `--scalp-once` only after all tests and dry-run checks pass, then inspect report, broker order, ledger, and SQLite intent.
- [ ] Do not claim profitability; report signal distribution, fill quality, realized P/L, and remaining risks.
