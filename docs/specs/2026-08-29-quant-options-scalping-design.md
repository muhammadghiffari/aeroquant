# Quant-Gated Hierarchical Options Scalping Design

> **Archived:** This proposal is superseded by
> `docs/specs/2026-09-01-deterministic-single-leg-momentum-design.md`.
> Scalping is not an executable mode in the current system.

## Goal

Use an intraday Quant signal as the entry gate while routing every actionable
scalp through the same hierarchical agent pipeline as the regular options flow.
The system remains paper-only. Python determines the deterministic direction
and hard risk limits; the LLM hierarchy supplies contextual interpretation and
the bounded strategy proposal.

## Strategy

The strategy evaluates the underlying on 1-minute and 5-minute bars. It uses
EMA9/EMA21, VWAP, RSI, ATR, short-term momentum, and relative volume. A signal
is actionable only when the primary and confirmation timeframes agree, clear
the configured factor scores, and the option quote is liquid. Missing/stale
data, extreme RSI, and insufficient activity remain hard blockers.

- Bullish signal: EMA9 above EMA21, price above VWAP, positive momentum, valid
  RSI range, and confirmation timeframe aligned.
- Bearish signal: the mirrored conditions, with price below VWAP and negative
  momentum.
- A timeframe conflict, stale data, extreme RSI, insufficient activity, or
  excessive option spread produces WAIT or rejection.

Direction is deterministic:

- Bullish -> LONG_CALL only.
- Bearish -> LONG_PUT only.
- Otherwise -> WAIT.

The LLM cannot change direction, option type, contract universe, quantity, or
risk budget.

## Hierarchy Integration

An actionable scalp is dispatched from `orchestrator.scalping_pipeline` to the
full `_process_symbol` path with `scalp_mode=true`:

1. Quant Engine produces the regular numeric report and the intraday scalp
   signal remains attached as primary evidence.
2. UnderlyingTrendAgent, VolatilityAgent, and NewsEarningsAgent interpret the
   report.
3. TechnicalManager and ContextManager compile the reports.
4. StrategyDecisionAgent (Chief) proposes a supported long call or long put.
5. RiskManagerAgent runs deterministic checks, followed by its LLM sanity
   review when all hard checks pass.
6. ScalpSupervisor may only approve or make bounded level changes before
   Execution Agent submission.

The deterministic Quant direction gate runs after the Chief and rejects any
proposal that disagrees with the actionable intraday direction. A non-actionable
Quant signal returns `WAIT` without calling the hierarchy.

BTC/USD is secondary context only. It can annotate alignment, confidence, and
sizing, but it cannot override the equity direction or strategy type.

## Model Tiers And Learning

When `LLM_PROVIDER=featherless`, light agents use `FEATHERLESS_LIGHT_MODEL`
(`zai-org/GLM-5.2`) and heavy agents use `FEATHERLESS_HEAVY_MODEL`
(`zai-org/GLM-5.3-Flash`). The provider remains single-shot and schema-bound;
no credentials are stored in this document.

Every live scalp cycle calls the evaluation hook after broker-facing actions.
Action counts are persisted for every cycle. Newly broker-confirmed closed
positions are synced to SQLite and written to post-mortem memory. When vector
embeddings are disabled or unavailable, text post-mortems remain durable and
still feed the lesson distiller; hard risk rules are never learned or changed.

Telegram notifications are idempotent and cover order submitted, order filled,
close requested, close filled with realized broker P/L, critical failures, and
one daily market-close summary using the exchange date.

## Contract And Execution

The quant selector chooses a liquid option from the deterministic direction's
candidate set, preferring 7-21 DTE and delta in a moderate directional range.
Expiry is allowed after the operational final-close date because American-style
options can be sold before expiry; the position manager must close it before
that deadline. Entry uses a bounded limit price based on the live ask. The
position is created as PENDING_ENTRY and reconciled from broker status.

Each trade has quant-generated entry, take-profit, stop-loss, and time-stop
levels. Exits use conservative bid-side valuation for long options and are
managed independently of the slow analysis loop.

## LLM Supervision

The supervisor receives only the quant feature summary, selected contract,
quote, and proposed levels. It can approve, veto, or make bounded level
adjustments. Entry changes are limited to +/-3%; take-profit and stop-loss
changes are limited to +/-10%. Every accepted change must name the input
features and rationale. Invalid, unexplained, or out-of-bound output is
discarded and the quant baseline is retained.

## Risk Controls

- Paper trading is mandatory.
- Maximum loss per trade is 0.5% of equity.
- Daily loss circuit breaker is 1.5% of equity.
- One active position per underlying.
- Maximum total active positions remains bounded.
- Cooldown follows a close or rejected signal.
- Stale quotes, missing bars, invalid prices, and broker disagreement fail
  closed.
- Manual closes are reconciled before any new entry is considered.

## Reporting

Every cycle report records the raw quant features, direction decision,
selected contract, baseline levels, LLM response, applied override (if any),
override reason, bound checks, broker order identifiers, and final status.

## Testing And Rollout

1. Unit tests cover indicator calculations, bullish/bearish symmetry, WAIT
   conditions, hierarchy dispatch, direction gating, contract selection, level
   bounds, evaluation fallback, Telegram deduplication, and broker reconciliation.
2. Full test suite and compile checks must pass.
3. Historical or fixture-based simulation compares call, put, and WAIT
   outcomes without sending orders.
4. Dry-run reports must show both LONG_CALL and LONG_PUT paths when fixtures
   contain both directions.
5. Paper execution is resumed only after the ledger is reconciled with the
   broker and a manual review of dry-run reports.

## Non-Goals

- No live trading.
- No unrestricted LLM strategy selection.
- No 0DTE or unlimited expiry chasing.
- No claim that a quant signal guarantees profit; performance must be measured
  from fill-based paper results.
