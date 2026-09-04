# Paper Runtime Reset and Telegram Monitoring Design

Status: approved in conversation on 2026-09-03

## Goal

Start the local AeroQuant runtime from an empty state, remove the artificial
shadow execution blocker, make paper execution observable end to end, and
deliver a Telegram event for each pipeline stage.

The system must remove runtime clutter without deleting source code, tests,
documentation, local credentials, or dependencies.

## Non-goals and boundaries

- This does not delete historical orders from Alpaca. Broker history is
  controlled by Alpaca and is not equivalent to local runtime state.
- This does not force an order when market data, candidate validity, buying
  power, liquidity, DTE, risk, account identity, or broker status is invalid.
- This does not expose API keys, Telegram tokens, raw prompts, or raw model
  responses through Telegram.
- Paper execution remains the only allowed execution mode.
- A limit order that remains pending is a valid broker outcome; the system
  must not pretend that it filled.

## Runtime reset

The reset operation runs in this order:

1. Verify the configured Alpaca account in read-only mode and require paper
   trading, the expected account ID, no open positions, and no open orders.
2. Stop the Momentum and Monitor scheduled tasks and verify their processes
   have exited.
3. Delete the contents of `state/`, `reports/`, and `runs/`, including old
   logs, databases, ledgers, memory artifacts, screenshots, and Telegram
   dedupe state.
4. Recreate the three directories empty. Runtime code creates fresh ledger,
   operational database, evaluation database, report, and notification files
   as needed.
5. Preserve source code, tests, docs, `.env`, `.env.example`, `.gitignore`,
   and dependency files.

The reset must abort before deletion if Alpaca reports an open position or
open order. The operation must not cancel or close broker objects implicitly.

## Execution policy

`SHADOW_ANALYSIS_ENABLED` defaults to `false` for autonomous paper mode.
When disabled:

- The pipeline does not record or route normal paper candidates through the
  shadow analysis lane.
- An actionable quant report uses `quant_gate["candidates"]` directly.
- `SHADOW_ONLY` is not emitted by the normal autonomous paper path.

The following gates remain mandatory:

- paper-only configuration and expected account identity;
- market clock and valid market data;
- quant entry actionability and candidate whitelist;
- strategy schema and candidate selection validation;
- risk budget, buying power, liquidity, and DTE checks;
- local order intent creation before the broker request;
- deterministic `client_order_id` lookup before retrying an ambiguous request;
- broker order status reconciliation and position reconciliation.

Entry candidates use a 7-21 day DTE window, and the risk layer uses the same
21-day ceiling. American-style positions may be sold at any time when the
exit policy triggers; the entry horizon does not restrict exits.

## Single-writer state

The entry loop and position monitor share a cross-process exclusive ledger
lock around their full load, mutate, and save transaction. Atomic file replace
continues to protect readers, but it is not used as a substitute for the
transaction lock. A monitor cycle must never overwrite an entry cycle's newer
ledger state.

## End-to-end flow

Each autonomous cycle follows this sequence:

1. `CYCLE_STARTED`: create a cycle ID and verify runtime prerequisites.
2. `ACCOUNT_PREFLIGHT`: verify paper mode, account identity, buying power,
   market clock, and Telegram health.
3. `POSITION_RECONCILIATION`: reconcile broker positions and unresolved order
   intents before new analysis.
4. `MARKET_DATA`: fetch and validate underlying, news, and option data.
5. `QUANT_COMPLETED`: calculate direction, confidence, actionability, and the
   exact candidate whitelist.
6. `LLM_STAGE_COMPLETED`: report each trend, volatility, news, technical,
   context, and chief strategy stage independently.
7. `STRATEGY_DECIDED`: report the selected candidate or `WAIT`.
8. `RISK_DECIDED`: report every deterministic risk check and the final
   approval/rejection.
9. `ORDER_INTENT_CREATED`: persist the local intent and its client ID before
   submitting anything.
10. `ORDER_SUBMITTED`: report the Alpaca order ID and broker status.
11. `ORDER_FILLED`, `ORDER_PENDING`, or `ORDER_REJECTED`: report the next
    broker-confirmed state from reconciliation.
12. `POSITION_RECONCILED`: report the local ledger and broker position view.
13. `CYCLE_COMPLETED`: report duration, outcome, and any delivery failures.

The canary runs one symbol and at most one order. The full watchlist starts
only after the canary's broker and Telegram acceptance checks pass.

## Telegram delivery

Telegram notifications use the existing credential boundary in `alerts.py`.
Each event has a deterministic ID containing the cycle ID, symbol, stage, and
sequence. The sender is idempotent and chunks messages below Telegram's size
limit.

Messages contain only operational fields: symbol, stage, status, durations,
candidate/order IDs, risk checks, and rejection reasons. The notification
layer writes failed deliveries to a local outbox and retries with bounded
backoff. It never retries an Alpaca order merely because notification failed.

Autonomous paper startup requires a Telegram credential and health check. A
transient Telegram failure after startup is reported locally and retried; it
does not duplicate or alter broker execution.

## Failure handling

- Invalid or stale data: stop that symbol before strategy/execution and report
  the reason.
- LLM timeout or invalid schema: fail closed for that stage and report the
  degraded cycle.
- Risk rejection: record and report the rejection; do not submit an order.
- Broker submit timeout: query by `client_order_id`; only create a new request
  when no broker order exists and the local intent is eligible for retry.
- Accepted/new/pending broker statuses remain unresolved until a later monitor
  reconciliation observes a terminal status.
- Telegram failure: persist the notification event and retry independently.
- Process crash: restart the worker, then reconcile broker state before new
  entries.

## Tests and acceptance criteria

### Automated tests

- Shadow-disabled actionable candidate reaches the normal risk/execution path.
- Non-actionable candidates still fail closed with an explicit wait/rejection.
- Reset refuses to delete state when an open broker position or order exists.
- Ledger lock serializes entry and monitor transactions.
- Telegram stage ordering, deterministic dedupe, chunking, outbox retry, and
  no-secret message content are covered.
- Alpaca submit ambiguity resolves through the same client order ID without a
  duplicate order.
- Existing test suite, syntax compilation, dependency check, and dashboard
  smoke test remain green.

### Canary acceptance

- Preflight confirms paper mode, expected account, market availability, and
  Telegram health.
- One cycle report contains all stages and a single cycle ID.
- Telegram receives the stage events in order without duplicates.
- If approved, broker returns an order ID matching the persisted intent.
- Monitor reports the actual broker status and does not create a duplicate.
- If no candidate passes, Telegram explains the exact wait/rejection reason;
  the canary is not marked failed merely because a safe cycle did not trade.
- Full watchlist automation is enabled only after the above checks pass.

## Operational rollback

Stop the two scheduled workers first. Disable autonomous mode in `.env` if
needed. Do not delete broker positions or cancel orders as part of rollback;
reconcile them through the position monitor. Local runtime reset can be
repeated only after the no-open-position/no-open-order guard passes.
