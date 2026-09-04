# Confidence Agent Design

## Goal

Improve the quality and usefulness of entry confidence without lowering any
existing safety threshold. The system must continue to fail closed and preserve
`WAIT` when the evidence is weak, incomplete, stale, or unvalidated.

The executable decisions remain `LONG_CALL`, `LONG_PUT`, and `WAIT`. The system
remains paper-only, single-leg, quantity `1`, and limited to the existing option
candidate whitelist.

## Non-Negotiable Rules

- `MOMENTUM_MIN_PROBABILITY_LB` remains exactly `0.55`.
- A lower-bound value below `0.55` cannot open a position.
- Confidence cannot override a Quant `WAIT` caused by invalid data, missing
  direction, insufficient samples, news risk, quote quality, candidate quality,
  or risk limits.
- DTE, delta, spread, quote freshness, expected value, news, risk, paper-only,
  and quantity gates remain unchanged.
- The LLM cannot edit direction, contract, expiry, strike, option type,
  quantity, risk budget, or any confidence result.
- BTC remains shadow telemetry only.

## Current Diagnosis

The current cycle behavior is internally consistent:

- `SPY` and `QQQ` have EMA alignment that is positive but 20-day momentum that
  is negative, so direction is `WAIT`.
- `AAPL`, `NVDA`, and `MSFT` have bullish direction but unconditional Wilson
  lower bounds below `0.55`.
- All five symbols have valid bars, no duplicate timestamps, finite closes, and
  positive volume.
- The pipeline correctly stops before LLM and execution when
  `entry_actionable` is false.

The limitation is statistical rather than an API or execution failure. The
existing probability estimator pools all historical regimes together. It does
not ask whether the current setup resembles the historical samples used to
estimate its success rate.

## Confidence Agent

The Confidence Agent is a deterministic Python component. It is not an LLM
decision-maker and cannot bypass the Quant authority.

For each completed daily-bar endpoint with enough history and five future bars:

1. Calculate EMA 10, EMA 22, 5-day return, 20-day return, 60-day return, and a
   20-day realized-volatility value using bars available at that endpoint only.
2. Derive the historical setup direction from EMA 10 versus EMA 22 and the
   sign of the 20-day return.
3. Assign a volatility regime using the endpoint's 20-day realized volatility
   relative to the rolling historical median available at that endpoint.
4. Keep only historical endpoints with the same setup direction and volatility
   regime as the current setup.
5. Measure the five-day forward compounded return against the existing target:
   `+0.0001` for bullish setups and `-0.0001` for bearish setups.
6. Calculate the one-sided Wilson lower bound from those conditioned samples.
7. Require at least `30` conditioned samples. Fewer samples produce
   `WAIT_SEE`, not a fallback to an easier estimate.

The conditioned lower bound is the entry confidence estimator and must be at
least `0.55`. The existing unconditional probability and lower bound remain in
the report under an audit section so the two estimators can be compared. The
implementation must not use `max(unconditional, conditioned)` or any other
fallback that hides a failed conditioned estimate.

## Multi-Horizon Confirmation

The current live setup must also pass a separate directional confirmation:

- Calculate the sign of the current 5-day, 20-day, and 60-day returns.
- Count how many horizon signs agree with the setup direction.
- Require at least `2 of 3` horizons to agree.
- Missing horizons or invalid values produce `WAIT_SEE`.

`confidence_score` is the diagnostic minimum of the setup lower bound and the
agreeing-horizon ratio, not an alternate bypass. An entry requires both
`setup_lower_bound >= 0.55` and `horizon_alignment >= 2 of 3`.

## Decision States

The Quant report will include a versioned `confidence` object:

```json
{
  "confidence_version": "setup-conditioned-momentum-v1",
  "state": "ENTER_CONFIRMED | WAIT_SEE | WAIT_DATA",
  "direction": "BULLISH | BEARISH | WAIT",
  "setup_probability": 0.0,
  "setup_lower_bound": 0.0,
  "setup_successes": 0,
  "setup_sample_size": 0,
  "horizon_alignment": {
    "agreeing": 0,
    "total": 3,
    "passed": false
  },
  "confidence_score": 0.0,
  "reasons": []
}
```

`WAIT_SEE` means data is valid but the current setup has insufficient evidence,
weak lower bound, or incomplete multi-horizon agreement. `WAIT_DATA` means the
Confidence Agent cannot calculate a valid result because bars, history, or
features are unusable. Downstream pipeline gates may report `WAIT_BLOCKED` for
news, quote, candidate, or risk failures. No wait state may reach execution.

## Pipeline Integration

The entry flow becomes:

1. Fetch exchange-date-correct daily bars and validate them.
2. Calculate the existing raw momentum metrics for audit.
3. Calculate the conditioned confidence and multi-horizon confirmation.
4. Return `WAIT_SEE` when confidence fails; do not call LLM agents.
5. Build the existing option whitelist only after confidence passes.
6. Apply unchanged quote, EV, news, LLM candidate-selection, risk, and executor
   gates.
7. Persist both raw and conditioned metrics in the cycle report and position
   metadata.

The daily-bar request must use `config.market_date()` in the exchange timezone,
not the laptop's local `date.today()`. This avoids requesting a future start
date during the part of the New York trading session that falls after midnight
in Jakarta.

The existing `vwap` report field is a volume-weighted average of daily closes
over the fetched history, not a session VWAP. It is not used by the entry
confidence gate and will not be used as a new confidence feature. A true
session-VWAP replacement is a separate, independently tested change.

## Testing and Audit

Before re-enabling non-dry-run entries:

- Add unit tests for conditioned sample selection, Wilson lower bound, minimum
  sample fail-closed behavior, 2-of-3 alignment, and decision states.
- Add a no-look-ahead test that changes future bars and proves historical
  confidence at an earlier endpoint does not change.
- Add a timezone-boundary test proving daily bars use the New York market date.
- Add a pipeline test proving confidence failure prevents LLM and execution.
- Add a regression test that asserts the configured threshold is still `0.55`.
- Run an offline audit over the current 400-day bars for every watchlist symbol.
- Report raw versus conditioned signal counts, sample sizes, successes,
  probabilities, lower bounds, horizon alignment, and each `WAIT` reason.
- Run `pytest -q` and `python -m compileall .`.
- Run a dry-run cycle and inspect the full report for whitelist and gate
  correctness.
- Reconcile Alpaca positions and non-terminal orders before rollout.

Underlying-only probability remains a signal diagnostic, not proof of options
profitability. Separate point-in-time option quote backtests are still required
for profitability claims.

## Rollout

During implementation and audit:

- Keep the position monitor active.
- Hold the entry task or run it in dry-run mode.
- Do not lower the threshold or add a force/bypass flag.
- Re-enable paper entry only after tests, no-look-ahead audit, report review,
  and broker reconciliation pass.

Live trading remains out of scope.
