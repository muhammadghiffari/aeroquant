# Paper Runtime Reset and Telegram Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reset local runtime data, remove the active shadow blocker, make paper execution single-writer and idempotent, and publish every pipeline stage to Telegram.

**Architecture:** Keep the existing sequential quant/LLM/risk/executor pipeline. Add a guarded reset command, disable shadow analysis in autonomous paper mode, serialize ledger transactions across the entry and monitor processes, and add durable stage notifications without coupling Telegram retries to broker order retries. Roll out with one-symbol canary before watchlist automation.

**Tech Stack:** Python 3.10, pytest, FastAPI/Uvicorn, alpaca-py, SQLite, JSON ledger, requests, PowerShell Scheduled Tasks, Telegram Bot API.

---

## File Map

- Create `scripts/reset_runtime.py`: guarded broker check and local runtime cleanup.
- Create `scripts/reset_runtime.ps1`: stop/verify scheduled workers before cleanup.
- Modify `config.py`: shadow and Telegram runtime flags.
- Modify `runtime_safety.py`: Telegram configuration and health checks.
- Modify `orchestrator/pipeline.py`: shadow-off routing, stage events, and locked state writes.
- Modify `execution/ledger.py`: cross-process transaction lock.
- Modify `main.py`: use the same ledger transaction boundary in the monitor.
- Modify `alerts.py`: stage events, durable outbox, bounded retry, and health check.
- Create or modify tests under `tests/` for every new behavior.
- Do not modify `.env` values or delete source, tests, docs, or dependency files.

## Task Sequence

### Task 1: Add reset guards and cleanup

Implement and test the read-only broker guard before any deletion.

**Files:**
- Create: `scripts/reset_runtime.py`
- Create: `scripts/reset_runtime.ps1`
- Test: `tests/test_reset_runtime.py`

- [ ] **Step 1: Write failing guard tests**

Test `broker_state_is_clear(account, positions, orders)` for an empty broker
and assert it raises `RuntimeError` for one open position or one non-terminal
order. Test `clear_runtime(paths)` removes files and directories but recreates
the three root directories.

The core assertions should be explicit:

```python
assert broker_state_is_clear(SimpleNamespace(id="acct-1"), [], []) is True
with pytest.raises(RuntimeError, match="open position"):
    broker_state_is_clear(SimpleNamespace(id="acct-1"), [{"symbol": "SPY"}], [])
with pytest.raises(RuntimeError, match="open order"):
    broker_state_is_clear(SimpleNamespace(id="acct-1"), [], [{"status": "new"}])
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run: `python -m pytest -q tests/test_reset_runtime.py`
Expected: FAIL because the reset module and guard functions do not exist yet.

- [ ] **Step 3: Implement the guarded reset helper**

Use fixed runtime roots from `config.REPORTS_DIR`, `config.STATE_DIR`, and
`config.BASE_DIR / "runs"`. Require a `--confirm-reset` flag. Call the
read-only trading client, verify `PAPER_TRADE`, expected account identity,
empty `get_all_positions()`, and no order whose status is not one of
`filled`, `canceled`, `expired`, `rejected`, or `replaced` after normalizing
enum names. Delete children with
`shutil.rmtree`/`unlink`, never delete the roots or `.env`.

- [ ] **Step 4: Add the Windows task wrapper**

In `reset_runtime.ps1`, use `Stop-ScheduledTask` for
`AeroQuant-Radith-Momentum` and `AeroQuant-Radith-Monitor`, wait until each
task is not `Running`, then invoke the project Python executable with
`scripts/reset_runtime.py --confirm-reset`. Stop on any nonzero exit code.

- [ ] **Step 5: Run the focused tests and review the deletion boundary**

Run: `python -m pytest -q tests/test_reset_runtime.py`
Expected: all reset guard and cleanup tests pass, with no paths outside the
three runtime roots modified.

### Task 2: Disable shadow as an autonomous blocker

Make actionable quant candidates use the normal execution path and preserve hard safety gates.

**Files:**
- Modify: `config.py:45-53`
- Modify: `orchestrator/pipeline.py:231-267`
- Modify: `tests/test_local_run.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing behavior tests**

Add a test that sets `config.SHADOW_ANALYSIS_ENABLED` to `False`, supplies an
actionable bullish quant report with a valid `shadow_candidates` list, and
asserts the normal risk result is returned rather than `SHADOW_ONLY`. Add a
test that the shadow store is not called when the flag is false. Add a config
assertion that both entry and risk DTE ceilings are 21 days.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run: `python -m pytest -q tests/test_local_run.py tests/test_config.py`
Expected: FAIL on the missing shadow flag and the current 14-day ceilings.

- [ ] **Step 3: Add explicit shadow-off configuration**

Add this configuration beside the existing shadow settings:

```python
SHADOW_ANALYSIS_ENABLED = os.getenv("SHADOW_ANALYSIS_ENABLED", "false").strip().lower() == "true"
```

Change the pipeline so shadow snapshot processing and `analysis_only` require
this flag. Keep the existing `not quant_gate.get("entry_actionable")` check,
and select `quant_gate.get("candidates", [])` for the normal paper path.

- [ ] **Step 4: Align the entry and risk DTE ceilings**

Set both `MOMENTUM_MAX_DTE` and `MAX_DTE` to `21`. Entries remain limited to
the 7-21 day horizon; American-style positions may exit at any time through
the existing TP/SL, event-risk, reversal, and reconciliation rules.

- [ ] **Step 5: Run the focused tests and review the execution boundary**

Run: `python -m pytest -q tests/test_local_run.py tests/test_config.py`
Expected: all shadow-off, actionable-routing, and DTE-consistency tests pass;
non-actionable reports still return an explicit wait/rejection.

### Task 3: Serialize ledger state

Prevent the monitor from overwriting entry-loop state while retaining atomic reads and writes.

**Files:**
- Modify: `execution/ledger.py`
- Modify: `main.py:71-80`
- Modify: `orchestrator/pipeline.py:96-109` and `:398-453`
- Test: `tests/test_ledger.py` and `tests/test_local_run.py`

- [ ] **Step 1: Write the failing lock test**

Add a process-level test that starts two writers against the same temporary
ledger, pauses the first after loading, and verifies the second cannot enter
the transaction until the first saves and releases. Also assert the monitor
uses the transaction context while calling `manage_positions` and `save`.

Every production mutation must have this shape:

```python
with ledger.ledger_transaction():
    data = ledger.load()
    mutate(data)
    ledger.save(data)
```

- [ ] **Step 2: Run the focused lock tests and verify failure**

Run: `python -m pytest -q tests/test_ledger.py tests/test_local_run.py -k lock`
Expected: FAIL because no shared transaction lock exists.

- [ ] **Step 3: Implement a cross-platform transaction lock**

Add `ledger_transaction()` to `execution/ledger.py`. Open
`config.STATE_DIR / "ledger.lock"` in append-binary mode, use `msvcrt.locking`
on Windows and `fcntl.flock` on POSIX, and always release/close the handle in
`finally`. Keep `save()` atomic; the lock protects the complete read-modify-
write transaction rather than replacing atomic rename.

- [ ] **Step 4: Lock monitor and entry commit sections**

Wrap `_monitor_once` load, mutation, and save in `ledger_transaction()`. Do not
hold the lock while LLM analysis runs. Before entry intent creation, reacquire
 the lock, reload the latest ledger, reject if the symbol became active or has
 an unresolved intent, recompute exposure/open-position counts, and rerun the
 hard risk checks with current state using `risk_decide(...,
 use_llm_sanity=False)`. Use that final decision's resolved legs for the
 request. Keep the lock through intent creation, broker submit, and position
 append/save; an exception leaves the intent for reconciliation.

- [ ] **Step 5: Verify state serialization**

Run: `python -m pytest -q tests/test_ledger.py tests/test_local_run.py -k "lock or monitor or persists_order_intent"`
Expected: all lock, monitor, and intent-ordering tests pass without changing
the existing public ledger JSON shape.

### Task 4: Add Telegram stage telemetry

Emit ordered, idempotent, secret-free events and retry failed delivery independently.

**Files:**
- Modify: `alerts.py`
- Modify: `runtime_safety.py`
- Modify: `orchestrator/pipeline.py`
- Modify: `main.py`
- Test: `tests/test_alerts.py`, `tests/test_runtime_safety.py`, and `tests/test_local_run.py`

- [ ] **Step 1: Write failing stage-event tests**

Add a test that emits stages `CYCLE_STARTED`, `QUANT_COMPLETED`,
`STRATEGY_DECIDED`, `RISK_DECIDED`, `ORDER_SUBMITTED`, and
`CYCLE_COMPLETED`, then asserts the sent event IDs are
`cycle-1:SPY:<stage>:<sequence>`. Assert the formatted messages do not contain
`API_KEY`, `SECRET_KEY`, `BOT_TOKEN`, or raw prompt text. Add a retry test that
leaves a failed event in the outbox and removes it after the next successful
flush.

The ordering assertion should use the public event contract:

```python
assert [event_id for _, _, event_id in sent] == [
    "cycle-1:SPY:CYCLE_STARTED:0",
    "cycle-1:SPY:QUANT_COMPLETED:1",
    "cycle-1:SPY:STRATEGY_DECIDED:2",
    "cycle-1:SPY:RISK_DECIDED:3",
    "cycle-1:SPY:ORDER_SUBMITTED:4",
    "cycle-1:SPY:CYCLE_COMPLETED:5",
]
```

- [ ] **Step 2: Run the focused alert tests and verify failure**

Run: `python -m pytest -q tests/test_alerts.py tests/test_runtime_safety.py -k "stage or outbox or telegram"`
Expected: FAIL because stage emission, outbox flushing, and Telegram health
checks are not yet implemented.

- [ ] **Step 3: Implement health checks and durable delivery**

Add `telegram_health_check() -> tuple[bool, str]` using Telegram `getMe` and
`getChat` with a five-second timeout. Add `emit_stage(...)` that writes a
compact JSON event to `state/telegram_outbox.jsonl` before attempting delivery.
Add `flush_telegram_outbox(max_events=25)` with atomic rewrite, bounded retry
metadata, and the existing deterministic dedupe IDs. Treat an already-sent
dedupe ID as delivered. Never include configuration values in messages.

- [ ] **Step 4: Require Telegram only for autonomous execution**

Extend `configuration_errors(..., require_telegram=False)` to report missing
`TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` without exposing values. In
non-dry `run_cycle`, require the credentials and health check before analysis.
Keep dry-run and unit-test analysis usable without Telegram. Call the same
health check before entering the long-running loop.

- [ ] **Step 5: Wire events at every pipeline boundary**

Emit one event after each completed stage, including explicit failure/skip
events. Use one sequence counter per cycle/symbol, call the notifier only in
non-dry mode, and flush after each event so Telegram receives the order in
which stages completed. Keep `notify_cycle_events` for broker lifecycle
events, but make stage events the canonical per-cycle trail.

- [ ] **Step 6: Run focused alert and pipeline tests**

Run: `python -m pytest -q tests/test_alerts.py tests/test_runtime_safety.py tests/test_local_run.py`
Expected: all event ordering, dedupe, outbox, health, and existing pipeline
tests pass.

### Task 5: Validate and run the canary

Run the full automated suite, reset only after the broker guard passes, start one-symbol paper canary, reconcile it, and enable the full watchlist only after acceptance checks.

**Files:**
- Create: `scripts/verify_canary.py`
- Create: `scripts/run_paper_canary.ps1`
- Test: `tests/test_canary.py`
- Verify: `reports/`, `state/`, Alpaca paper account, and Telegram chat

- [ ] **Step 1: Write canary verification tests**

Test that a canary report has one symbol, one cycle ID, no more than one
`ORDER_SUBMITTED`/`EXECUTED` result, and an execution client ID when an order
was submitted. Test that a safe `WAIT` or `REJECTED` result is accepted only
when it contains a nonempty reason.

The verifier must reject an unsafe multi-symbol or multi-order report:

```python
assert len(report["symbols"]) == 1
executions = [r for r in report.get("results", [])
              if r.get("action") in {"ORDER_SUBMITTED", "EXECUTED"}]
assert len(executions) <= 1
if executions:
    assert executions[0]["execution"]["client_order_id"]
```

- [ ] **Step 2: Run the canary tests and full local verification**

Run: `python -m pytest -q`; `python -m compileall -q .`; `python -m pip check`.
Expected: all tests pass, compilation is silent, and pip reports no broken
requirements.

- [ ] **Step 3: Execute the guarded reset**

Run from an elevated PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\reset_runtime.ps1
```

Expected: both scheduled tasks stop, the broker guard passes, and only the
contents of `state/`, `reports/`, and `runs/` are removed. Verify those roots
are empty before starting a worker.

- [ ] **Step 4: Run the one-symbol paper canary**

Run during market hours without `--force`:

```powershell
python .\main.py --once --symbol SPY
python .\scripts\verify_canary.py --symbol SPY
```

The verifier must confirm paper mode, expected account ID, Telegram delivery,
one cycle report, and broker reconciliation by `client_order_id`. A pending
limit order must be reported as pending, not treated as a fill. Before running,
verify `config.FINAL_CLOSE_DATE > config.market_date()` and that the configured
date is the intended paper-test horizon; do not silently trade with an expired
close date.

- [ ] **Step 5: Start the long-running workers only after canary approval**

Start the registered Momentum and Monitor tasks, verify exactly one running
instance of each, and inspect the first cycle's Telegram event sequence. Do
not start the full watchlist when any acceptance check fails. Stop both tasks
and reconcile broker state before investigating a failure.

- [ ] **Step 6: Perform final verification and handoff**

Run the dashboard smoke test through the local server wrapper, query the paper
account read-only for positions/orders, inspect the newest cycle report, and
confirm Telegram received the cycle completion event. Record the canary cycle
ID and broker order ID without recording secrets.
