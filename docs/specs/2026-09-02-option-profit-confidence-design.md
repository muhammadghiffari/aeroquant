# Option-Profit Confidence Design

## Goal

Expose two honest confidence lanes: `contract_confidence` represents the
probability that the selected long option is profitable after entry ask, exit
bid, spread, slippage, theta, and the configured exit horizon; `entry_confidence`
uses real historical stock bars for a clearly labelled paper proxy while the
contract lane is still calibrating. Underlying direction remains a required
feature and consistency gate, not a hidden profitability claim.

## Non-negotiable rules

- Final contract lower-bound threshold is `0.60`; the paper proxy has its own
  explicit raw historical setup-probability floor.
- Paper-only, one BUY leg, quantity `1` remain unchanged.
- LLMs may explain and choose only from the Quant whitelist; they cannot alter
  confidence, direction, contract, price, quantity, or risk limits.
- Stale, missing, invalid, or insufficient data fails closed.
- Current option-profit confidence is provisional until point-in-time shadow
  outcomes provide at least 30 matched observations.

## Data and scoring

The Quant report will expose underlying features (direction, 5/20/60 alignment,
momentum, volatility regime, expected move) and contract features (bid, ask,
spread, quote age, delta, DTE, IV, theta, last-trade size, and open interest).
The contract evaluator calculates conservative entry cost, projected exit bid,
theta decay, breakeven underlying move, and net P&L. A last-trade size is an
activity proxy, not a daily volume claim.

Shadow observations store the candidate snapshot and resolve after the same
one-bar horizon using the later contract bid. Labels are `net_pnl > 0`, with
spread and contract multiplier included. Matching observations are grouped by
direction, volatility regime, DTE bucket, and delta bucket; Wilson lower bound
and sample size are reported.

## States and rollout

- `GREEN`: calibrated contract lower bound `>=0.60`, at least 30 outcomes,
  underlying direction agrees, 2-of-3 horizons pass, positive EV, and all
  existing quote/news/risk gates pass. This is the quote-backed state.
- `GREEN_PROXY`: real 1H stock-bar history has at least 30 conditioned samples,
  a raw setup probability at or above the proxy floor, and 2-of-3 horizon
  alignment. The current option quote must be valid/fresh and within liquidity
  limits, but current ask/bid is not historical calibration; live bid/ask size
  is advisory activity context for the LLM and entry decision.
- `SHADOW_ONLY`: real candidate data exists but no executable confidence lane
  passes; LLM agents may still analyze and select a candidate, with no executor.
- `WAIT_SEE`: real historical setup exists but does not meet the proxy floor or
  horizon alignment.
- `WAIT_DATA`: missing or stale contract/underlying data; submit no order.

The pipeline records shadow data only in non-dry-run mode, keeps the existing
monitor active, keeps contract confidence separate from the stock-history entry
proxy, and never labels the proxy as calibrated option profitability.

## Hourly urgency mode

Daily bars remain the slow context layer and chart source. Completed `1Hour`
historical stock bars become the entry-analysis layer, with the same 5/20/60
bar features interpreted as hours. Shadow observations use a separate `1H`
timeframe and resolve after one completed hourly bar using the later live option
bid. Existing daily observations remain isolated and archived by their `1D`
timeframe and calibration version.

The five-minute scheduler refreshes live quotes and reruns analysis; it does
not create duplicate observations for the same completed hourly bar. Historical
stock OHLCV is suitable for underlying trend and visual charts, but it cannot
reconstruct historical option bid/ask P&L. The stock-history proxy is therefore
a separate paper strategy; the option-profit lane remains telemetry and does
not block proxy entry. Live option bid/ask is used for quote validity, spread,
order pricing, and activity context only. LLM agents run for both lanes and
remain required to select a whitelisted candidate before any paper order.
