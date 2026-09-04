# Deterministic Single-Leg Momentum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the active scalp entry system with a Quant-first, paper-only single-leg momentum system whose LLM can select only a Quant-approved option contract and whose exits are deterministic TP/SL plus validated reversal protection.

**Architecture:** Quant Engine is the authoritative entry gate and emits a versioned report with data quality, empirical probability, expected value, and an exact contract whitelist. LLM agents interpret Quant and news context, then return only a whitelisted candidate; Python validates the response again. A regular entry loop and a separate deterministic position monitor handle entries and exits without scalp-specific behavior.

**Tech Stack:** Python 3.10+, pandas, numpy, alpaca-py, pytest, JSON ledger, SQLite operational store, existing LLM providers.

---

## File Map

- Create: `quant_engine/momentum.py` for deterministic signal, probability, candidate scoring, and reversal features.
- Modify: `quant_engine/engine.py` to attach the versioned Quant gate and candidates.
- Modify: `data_engine/option_data.py` to expose exact candidate records and quote-quality metadata.
- Modify: `agents/strategy_decision_agent.py` to restrict output to candidate IDs and fail closed.
- Modify: `agents/risk_manager_agent.py` to enforce Quant direction, whitelist membership, and statistics gates.
- Modify: `orchestrator/pipeline.py` to remove scalp branches and run only the canonical flow.
- Modify: `execution/exit_policy.py` and `execution/position_manager.py` for fill-based TP/SL and reversal exits.
- Modify: `execution/executor.py` and `execution/ledger.py` for regular single-leg orders and safe numeric serialization.
- Modify: `main.py` and `config.py` to remove scalp entry modes and retain only regular entry plus monitor modes.
- Delete: `orchestrator/scalping_pipeline.py`, `quant_engine/scalping.py`, `agents/scalp_supervisor.py` after replacement tests pass.
- Modify: `docs/PRD.md`, `docs/AGENTS.md`, and the scalp design document to describe the canonical flow.
- Create: `docs/specs/2026-09-01-deterministic-single-leg-momentum-design.md` for the approved contract and rollout gates.
- Create/modify: `tests/test_momentum_quant.py`, `tests/test_contract_whitelist.py`, `tests/test_reversal_exit.py`, and existing lifecycle/ledger tests.

## Implementation Order

### Task 1: Freeze Runtime And Capture Regression Baseline

**Files:** `main.py`, `config.py`, `tests/test_ledger.py`, `tests/test_config.py`

- [x] Stop the existing scalp scheduled task and remove its lock before changing the entry path.
- [x] Run the existing ledger regression test and record the expected failing serialization error before changing production code.
- [x] Preserve `--once`, `--loop`, `--monitor`, and `--dry-run`; change no broker credentials.

Run:

```text
pytest tests/test_ledger.py::test_save_normalizes_numpy_integer_scalars -q
Expected: FAIL with TypeError: Object of type int32 is not JSON serializable
```

### Task 2: Implement The Quant Gate

**Files:** Create `quant_engine/momentum.py`; modify `quant_engine/engine.py`; test `tests/test_momentum_quant.py`.

- [x] Write failing tests for bullish, bearish, and insufficient-data `WAIT` decisions.
- [x] Write failing tests requiring empirical sample count and a conservative lower confidence bound before an actionable signal.
- [x] Implement pure functions with no network calls:

```python
def build_momentum_signal(bars, *, min_samples=30) -> dict: ...
def estimate_target_probability(returns, required_move, horizon) -> dict: ...
def score_reversal(features, previous_features) -> dict: ...
```

- [x] Include `quant_version`, `direction`, `probability`, `probability_lower_bound`, `sample_size`, `expected_value_after_costs`, `data_quality`, and explicit reject reasons.
- [x] Keep BTC out of the hard decision; expose it only as optional shadow context.
- [ ] Make missing/stale bars, invalid quote fields, insufficient samples, non-positive expected value, and unresolved earnings risk produce `WAIT`.

### Task 3: Build And Validate The Contract Whitelist

**Files:** `data_engine/option_data.py`, `agents/strategy_decision_agent.py`, `agents/risk_manager_agent.py`; tests `tests/test_contract_whitelist.py`.

- [x] Write failing tests proving a proposal with an unknown symbol, wrong option type, wrong direction, or changed expiry is rejected.
- [x] Write a test proving the LLM may choose only `candidate_id` values emitted by Quant.
- [x] Add candidate fields for exact symbol, direction, DTE, delta, IV/HV, bid, ask, spread, quote timestamp, probability, expected value, and reject reasons.
- [x] Generate candidates deterministically from the live chain; require fresh two-sided quotes, allowed DTE, delta bounds, liquidity, and a positive Quant edge.
- [x] Change Chief output to contain `candidate_id`, `strategy_type`, and rationale; never accept free-form strike/expiry construction.
- [x] Make risk validation re-resolve the exact symbol and require membership in the current Quant whitelist before execution.
- [x] Remove self-correcting revisions that ask the LLM to invent different strategies; invalid or rejected proposals become `WAIT`.

### Task 4: Replace The Entry Pipeline

**Files:** `orchestrator/pipeline.py`, `execution/executor.py`, `config.py`, `main.py`; tests `tests/test_local_run.py`, existing pipeline tests.

- [x] Write failing tests proving only `LONG_CALL`, `LONG_PUT`, or `WAIT` can reach execution and that BTC context cannot change direction.
- [x] Remove `scalp_mode`, `scalp_signal`, scalp levels, `ScalpSupervisor`, and strategy-capability research candidates from the execution path.
- [x] Run Quant before LLM agents; skip all LLM entry calls when the Quant gate is `WAIT`.
- [x] Keep news analysis as grounded context. `CRITICAL` news blocks entry; missing/low-confidence news analysis fails closed.
- [x] Use regular cycle timing for entries and leave the faster `--monitor` process only for position safety.
- [x] Persist Quant report, candidate whitelist, LLM candidate choice, risk checks, and strategy version in the cycle report and position.
- [x] Fix `execution/ledger.py` serialization with a small recursive JSON-normalization helper covering NumPy scalars, arrays, Decimal, dates, and datetimes.

### Task 5: Implement Deterministic TP/SL And Reversal Monitoring

**Files:** `execution/exit_policy.py`, `execution/position_manager.py`, `execution/executor.py`; tests `tests/test_reversal_exit.py`, `tests/test_exit_policy.py`, `tests/test_position_reconciliation.py`.

- [x] Write failing tests for actual-fill TP/SL, bid-side valuation, two-of-three reversal confirmation across two completed bars, and no close on a single noisy indicator.
- [x] Remove all scalp time-stop behavior and scalp exit reason names.
- [x] Re-anchor entry debit and TP/SL after broker fill; never preserve requested-entry levels after a fill differs.
- [x] Implement deterministic exit precedence: final deadline/expiry safety, hard SL/TP, critical news, confirmed reversal, then hold.
- [x] Treat stale/wide quotes as unsafe for new entries and alerting, not as a false reversal signal.
- [x] Resolve `CLOSING` orders to `CLOSED` only after a broker fill; return rejected/canceled closes to `OPEN` with retry metadata and bounded alerts.
- [x] Keep close orders paper-only, idempotent, and priced from executable bid/ask data.

### Task 6: Remove Scalp Surface And Update Documentation

**Files:** Delete scalp modules/tests; modify `config.py`, `main.py`, docs, and scheduler scripts.

- [x] Remove scalp CLI flags, lock file handling, scalp-only config, scalp supervisor, active scalp reports, and scalp tests; retain historical reports as archived artifacts.
- [x] Keep `quant_engine.btc_context` only as optional shadow telemetry behind a disabled-by-default flag.
- [x] Update `docs/PRD.md`, `docs/AGENTS.md`, and `docs/specs/2026-08-29-quant-options-scalping-design.md` so no document claims that scalp is executable.
- [ ] Update Windows scheduled tasks to run regular entry and monitor commands only; stop the old scalp task before paper execution resumes. The old task is stopped but remains registered/enabled; disabling or deleting it requires administrator access.

### Task 7: Options Backtest And Paper Rollout Gate

**Files:** `backtest_engine.py`, `tests/test_backtest_engine.py`, `docs/specs/2026-09-01-deterministic-single-leg-momentum-design.md`, run artifacts under `runs/`.

- [x] Write failing tests for option quote normalization, bid/ask fill modeling, non-fill, slippage, fees, expiry, and separate call/put metrics.
- [x] Add an options simulation path that consumes point-in-time underlying bars plus option quote snapshots; refuse to label underlying-only simulation as options validation.
- [ ] Run `LONG_CALL` and `LONG_PUT` backtests separately, then combined portfolio walk-forward/out-of-sample.
- [ ] Report expectancy after costs, win/loss distribution, profit factor, max drawdown, calibration, fill rate, and sample size.
- [ ] Do not resume non-dry-run paper entries until tests, compile checks, ledger/broker reconciliation, and manual report review pass.

## Verification Commands

```text
pytest -q
python -m compileall agents data_engine execution orchestrator quant_engine tests
python main.py --once --symbol SPY --dry-run --force
python main.py --monitor --interval 1 --dry-run
```

Expected final gate: zero test failures, compile exit code 0, dry-run reports show only Quant-whitelisted contracts, and no scalp command or report is produced.
