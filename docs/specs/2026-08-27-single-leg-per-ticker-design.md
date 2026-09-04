# Single-Leg Per-Ticker Design

## Goal

Allow multiple active underlying symbols, while each symbol has at most one active
long-options position: `LONG_CALL`, `LONG_PUT`, or `WAIT`.

## Entry Rules

- New proposals may contain exactly one `BUY` option leg.
- `LONG_CALL` requires one call; `LONG_PUT` requires one put.
- Credit spreads, debit spreads, iron condors, and naked short options are rejected.
- A ticker with an `OPEN`, `PENDING_ENTRY`, `CLOSING`, or `RECOVERY_REQUIRED`
  record cannot create another entry.
- Different tickers may hold one long option each, subject to the existing total
  portfolio limits and deterministic risk rules.

## Lifecycle

The position monitor continues to use strategy-level P/L, not a single leg P/L.
For a long option, it submits a close only when the configured profit target,
loss limit, or pre-expiry rule triggers. A new dashboard row represents one
ticker and one option contract.

## Existing Positions

The new policy affects future entries only. Existing broker positions must be
closed or reconciled before a new entry for that ticker is allowed.
