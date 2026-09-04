# Confidence Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pooled momentum probability gate with a causal setup-conditioned confidence gate while keeping the lower-bound threshold at `0.55`.

**Architecture:** Keep the existing raw momentum signal as an audit metric. Add a pure deterministic confidence module that computes historical setup features using only data available at each endpoint, conditions forward outcomes by direction and volatility regime, and requires 2 of 3 current horizons to agree. The pipeline will not call LLM or execution until this gate and the existing option/risk/news gates pass.

**Tech Stack:** Python 3.10+, NumPy, pandas, pytest, Alpaca Market Data API, existing JSON reports and Windows Scheduled Tasks.

---

## File Map

- Create: `quant_engine/confidence.py` for causal setup-conditioned probability and horizon confirmation.
- Modify: `data_engine/stock_data.py` to use `config.market_date()` for daily-bar requests.
- Modify: `quant_engine/engine.py` to attach raw and conditioned metrics and build candidates from the conditioned gate.
- Modify: `orchestrator/pipeline.py` to persist confidence states and preserve `WAIT_SEE` before LLM calls.
- Modify: `agents/risk_manager_agent.py` to validate direction from confirmed confidence while rejecting unconfirmed states.
- Create: `tests/test_confidence.py` for estimator, causal, and horizon behavior.
- Modify: `tests/test_stock_data.py` for the New York date boundary.
- Modify: `tests/test_momentum_quant.py` and `tests/test_local_run.py` for the new report shape and gate.
- Create: `scripts/audit_confidence.py` to generate the read-only comparison artifact.
- Create: `runs/confidence_audit_2026-09-01.json` as the offline comparison artifact.

## Safety Checkpoint

The entry task `AeroQuant-Radith-Momentum` must be stopped before code reload or
audit. The monitor task `AeroQuant-Radith-Monitor` remains active. Before stopping
entry, query Alpaca and require zero non-terminal orders unless an existing order
is explicitly reconciled. Do not use `--force`, lower a threshold, or enable live
trading.

Use `Stop-ScheduledTask -TaskName AeroQuant-Radith-Momentum` for the entry
freeze; do not stop the monitor task.

## Task 1: Freeze Entry And Add Data Boundary Test

**Files:** `data_engine/stock_data.py`, `tests/test_stock_data.py`

- [x] Stop only the momentum entry task and confirm the monitor remains `Running`.
- [x] Write a failing test that supplies a fake market-date provider returning
  `2026-09-01`, makes the laptop `date.today()` return `2026-09-02`, invokes
  `get_daily_bars`, and asserts the request start is `2025-07-28` for a 400-day
  lookback rather than the laptop's local date.
- [x] Run `pytest tests/test_stock_data.py -q` and observe the test fail because
  the implementation still calls `date.today()`.
- [x] Import `config` in `stock_data.py` and replace the request's `start=` value
  with `config.market_date() - timedelta(days=days)`.
- [x] Run `pytest tests/test_stock_data.py -q`; expect all tests to pass.

## Task 2: Add Causal Confidence Functions

**Files:** `quant_engine/confidence.py`, `tests/test_confidence.py`

- [x] Add tests first for a strongly trending synthetic series, a regime with
  fewer than 30 matching samples, 2-of-3 horizon confirmation, and invalid data.
- [x] Add a no-look-ahead test: calculate confidence at endpoint `t`, mutate all
  closes after `t + horizon`, recalculate the endpoint result, and assert the
  result at `t` is unchanged.
- [x] Run `pytest tests/test_confidence.py -q` and verify the new tests fail for
  missing `build_confidence_signal`.
- [ ] Implement these public functions:

```python
def build_confidence_signal(
    bars,
    *,
    min_samples: int = 30,
    horizon: int = 5,
    as_of_index: int | None = None,
) -> dict:
    """Return a causal conditioned confidence decision."""


def confirm_horizons(closes, direction: str) -> dict:
    """Return signs and the 2-of-3 result for 5, 20, and 60 bars."""
```

- [x] Calculate each historical endpoint's EMA 10/22, 20-day return, 5/20/60
  returns, and 20-day realized volatility only from its prefix.
- [x] Use the rolling median of prior endpoint volatility values to label the
  endpoint as `LOW` or `HIGH`; do not use future volatility.
- [x] Match the current direction and volatility label, then evaluate the
  existing 5-bar target of `+0.0001` for bullish or `-0.0001` for bearish.
- [x] Reuse the existing one-sided Wilson calculation from
  `quant_engine.momentum`; return `WAIT_DATA` for invalid history or unusable
  features, and return `WAIT_SEE` when valid conditioned samples are fewer than
  30.
- [x] Return `WAIT_SEE` for valid data with lower bound below `0.55`, direction
  `WAIT`, or fewer than 2 agreeing horizons. Return `ENTER_CONFIRMED` only when
  both gates pass.
- [x] Set `confidence_score` to the diagnostic minimum of the setup lower bound
  and the agreeing-horizon ratio; never use it as a replacement threshold.
- [x] Run `pytest tests/test_confidence.py -q`; expect all new tests to pass.

## Task 3: Integrate The Gate Without Changing Existing Safety Rules

**Files:** `quant_engine/engine.py`, `orchestrator/pipeline.py`, `agents/risk_manager_agent.py`, `tests/test_local_run.py`, `tests/test_momentum_quant.py`, `tests/test_contract_whitelist.py`

- [x] Add an integration test proving a conditioned `WAIT_SEE` returns before
  LLM calls and does not create candidates or order intents.
- [x] Run that test and observe failure against the current pipeline behavior.
- [x] In `build_quant_report`, retain the current raw `momentum` fields, attach
  the versioned `confidence` object, and create a candidate-signal adapter whose
  `probability_lower_bound` is the conditioned `setup_lower_bound`.
- [x] Build the existing option whitelist only when confidence is
  `ENTER_CONFIRMED`; preserve DTE, delta, spread, quote age, EV, and direction
  checks unchanged.
- [x] Set `entry_actionable` only when confidence and candidate whitelist pass.
- [x] Keep `WAIT_SEE` in the cycle report and map downstream quote/news/risk
  failures to the existing blocked action names.
- [x] Make risk validation use the confirmed confidence direction when present,
  and reject any non-confirmed confidence state even if `entry_actionable` is
  malformed or manually supplied.
- [x] Preserve raw unconditional probability under an explicit audit field; do
  not use `max(raw, conditioned)` or a fallback estimate.
- [x] Run the focused integration tests and expect them to pass.

## Task 4: Update Regression Tests And Reports

**Files:** `tests/test_momentum_quant.py`, `tests/test_local_run.py`

- [x] Update fixtures to include `confidence.state`, `setup_lower_bound`, and
  `horizon_alignment` without removing raw momentum assertions.
- [x] Add a test asserting `config.MOMENTUM_MIN_PROBABILITY_LB == 0.55`.
- [x] Add a test that rejects a confidence lower bound of `0.5499` and accepts
  exactly `0.55` only when all other gates pass.
- [x] Add a test that candidate probability uses the conditioned lower bound
  while the raw audit lower bound remains separately persisted.
- [x] Run the focused tests and then `pytest -q`.

## Task 5: Offline Signal Audit

**Files:** `scripts/audit_confidence.py`, `runs/confidence_audit_2026-09-01.json`

- [x] Implement `scripts/audit_confidence.py` to fetch the current 400-day bars
  and serialize one JSON row per watchlist symbol without touching broker state.
- [x] Run a read-only audit over the current 400-day bars for `SPY`, `QQQ`,
  `AAPL`, `NVDA`, and `MSFT`.
- [x] Record raw direction/lower bound, conditioned direction/lower bound,
  successes, sample size, volatility regime, horizon agreement, state, and
  reasons for every symbol.
- [x] Verify no endpoint's result changes when bars after its forward horizon are
  replaced; fail the audit if any look-ahead difference is found.
- [x] Compare raw versus conditioned actionable counts. A higher count is not
  sufficient for approval; every actionable row must satisfy `>= 0.55`, at least
  30 samples, 2-of-3 alignment, and unchanged option gates.

## Task 6: Final Verification And Rollout

- [x] Keep entry task stopped and monitor task active during audit review.
- [x] Run `pytest -q` and `python -m compileall .`.
- [x] Run one dry-run full-watchlist cycle and inspect the report for the new
  confidence object and exact whitelist.
- [x] Query Alpaca and require zero non-terminal orders or reconcile each one.
- [x] Run `Start-ScheduledTask -TaskName AeroQuant-Radith-Momentum` and confirm
  its state is `Running` with a new full-watchlist report.
- [x] Confirm `AeroQuant-Radith-Monitor` remains `Running`.
- [x] Confirm both tasks remain `Enabled`, `AtStartup`, `RunLevel Limited`, and
  have the configured restart policy.
- [x] Do not claim improved profitability; underlying-only confidence remains a
  diagnostic until point-in-time option quote backtests pass.
