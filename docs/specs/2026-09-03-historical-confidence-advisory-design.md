# Historical Confidence Advisory Design

Status: approved in conversation on 2026-09-03; implementation amendment awaiting file review

## Goal

Allow the live paper pipeline to evaluate current, valid option candidates even
when historical probability or option-outcome calibration is weak. Historical
measurements remain visible to the agents and reports as advisory evidence, but
they cannot alone veto an entry.

## Scope

This amendment changes only the role of historical confidence and calibration:

- Historical probability, Wilson lower bounds, proxy floors, and missing matched
  option outcomes become advisory fields and ranking inputs.
- Historical OHLCV bars remain available for calculating the current momentum
  features and directional bias. They are not treated as a guarantee of profit.
- Current option-chain data remains the candidate source.

## Mandatory Safety Gates

The following remain hard blockers:

- paper-only mode and expected account identity;
- market clock and valid current market data;
- current quote freshness, positive bid/ask, spread, delta, and DTE 7-21;
- exact quant whitelist and single-leg strategy schema;
- buying power, max loss, exposure, liquidity, and deterministic risk checks;
- order intent persistence, client-order idempotency, broker reconciliation, and
  position reconciliation;
- valid LLM proposal and final risk approval;
- Telegram/autonomous runtime preflight.

No threshold is lowered to force a trade. A candidate must still be valid from
current market data and pass every non-historical gate.

## Pipeline Behavior

The quant layer will expose historical confidence with an explicit advisory
label. If current direction and option-chain candidates are valid, candidate
construction may continue even when historical confidence is below its
threshold or contract history has fewer than the target samples. The historical
values remain attached to each candidate and are supplied to the LLM as context.

The strategy agent may choose `WAIT` for any reason, including weak historical
evidence, but Python will not convert a candidate to `WAIT` solely because of
that evidence. Invalid or degraded LLM output remains fail-closed and cannot
submit an order.

## Reporting

Reports and Telegram events will distinguish:

- `historical_confidence`: advisory probability, lower bound, sample size, and
  calibration state;
- `live_candidate_gates`: current quote, spread, delta, DTE, and data freshness;
- `entry_actionable`: whether a current candidate is eligible for strategy and
  risk evaluation.

This prevents a proxy signal from being presented as calibrated option P/L.

## Tests and Rollout

- Add a failing test proving a current valid candidate reaches strategy/risk when
  historical confidence is below threshold.
- Preserve tests proving current quote, DTE, spread, direction, whitelist, and
  risk failures still reject.
- Run the full suite, compile check, dependency check, and dashboard smoke test.
- Stop both live workers before changing production behavior.
- Run one fresh `SPY` canary, reconcile it, and inspect Telegram before resuming
  the full watchlist.
