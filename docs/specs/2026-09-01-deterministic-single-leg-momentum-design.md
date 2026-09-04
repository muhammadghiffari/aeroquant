# Deterministic Single-Leg Momentum Design

## Goal

Run a paper-only options system where Statistics and Quant are the decision
authority. The executable strategies are `LONG_CALL`, `LONG_PUT`, and `WAIT`.

## Decision Authority

`QuantEngine` produces a versioned report from completed underlying bars and the
current option chain. It calculates trend alignment, empirical forward-target
probability, a one-sided Wilson lower bound, option liquidity, bid/ask cost, and
expected value after spread and slippage. Missing data, insufficient samples,
low lower-bound probability, or non-positive expected value produces `WAIT`.

The Quant report also contains an exact candidate whitelist. Each candidate has
its option symbol, type, expiry, DTE, delta, IV, bid, ask, spread, quote time,
probability lower bound, and expected value. A candidate must pass the configured
DTE, delta, quote freshness, spread, and probability gates.

## LLM Boundary

LLM agents interpret Quant and grounded news. The Chief may select only one
`candidate_id` from the current whitelist. It cannot create or modify a symbol,
strike, expiry, option type, quantity, direction, or risk budget. Python checks
the exact candidate a second time immediately before risk evaluation.

BTC/USD is optional shadow telemetry. It cannot create, override, or resize a
trade until a separate statistical validation proves incremental edge.

## Entry Flow

1. Check market clock, account identity, and paper-only configuration.
2. Reconcile existing broker positions and unresolved order intents.
3. Run the deterministic Quant gate.
4. Return `WAIT_QUANT_GATE` without LLM calls when Quant is not actionable.
5. Fetch grounded news and run the analysis hierarchy.
6. Let the Chief choose one exact Quant candidate or `WAIT`.
7. Revalidate direction, candidate membership, single BUY leg, and risk limits.
8. Submit one paper limit order with an idempotent client order ID.

## Exit Flow

TP and SL are calculated from the broker's actual average fill. Long-option
valuation uses the executable bid. The fixed scalp time stop is not used.

The position monitor runs independently of entry analysis and applies this
precedence:

1. Final deadline and pre-expiry safety close.
2. Hard stop-loss or take-profit.
3. Grounded `CRITICAL` news close.
4. Confirmed reversal: two completed bars, with at least two of EMA regime,
   VWAP position, and momentum agreeing against the position.
5. Hold.

Stale, malformed, or excessively wide quotes cannot trigger a reversal. Close
orders remain pending until broker fill confirmation; rejected close orders
return to `OPEN` with retry metadata.

## Validation

Backtests must use point-in-time option quotes and separately report `LONG_CALL`
and `LONG_PUT`. The simulator includes ask-side entry, bid-side exit, spread,
slippage, fees, non-fill, TP/SL, expiry, fill rate, expectancy, win rate,
profit factor, drawdown, calibration, and sample size. Underlying-only results
are diagnostics and are not options profitability evidence.

Paper entry resumes only after all tests and compile checks pass, the local
ledger is reconciled with Alpaca, and dry-run reports show only whitelisted
contracts. Live trading is out of scope.
