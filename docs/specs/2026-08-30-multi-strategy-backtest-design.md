# Multi-Strategy Historical Backtest Design

**Goal:** Compare the available options strategies over the last five years on the current tech/BTC-sensitive universe without inventing unavailable market data.

## Scope

- Period: five years ending at the latest complete trading day.
- Universe: NVDA, AMD, MSTR, COIN, TSLA, AAPL, MSFT.
- Initial equity: $100,000.
- Risk: 0.5% per trade; 1.5% daily loss limit.
- Data: Alpaca CLI historical data; regular US equity hours; crypto BTC/USD aligned to equity sessions.
- Fill: next available bid/ask after signal; no same-bar look-ahead; conservative intrabar stop/target ordering.
- Baseline: deterministic, no LLM calls. Featherless ranking is a later ablation.

## Strategy Tracks

- Directional long call/put: existing EMA/VWAP/RSI/momentum signal, 7-21 DTE, delta 0.45-0.70, bid/ask-aware exits.
- Event volatility: event-date and implied-move comparison; executable only when historical option structures and quotes are available.
- American mispricing / relative value: American-style valuation and paired-leg convergence; otherwise unavailable.
- Gamma scalping: option plus stock hedge simulation using quote-level data; otherwise unavailable.
- Put-call parity: stock/call/put synchronized simulation including rates/dividends and costs; otherwise unavailable.

## Outputs

Each run stores raw data, normalized data, strategy specs, trades, equity curves, benchmarks, warnings, fee source, and fingerprints. Reports include returns, drawdown, trades, win rate, Sharpe, profit factor, fees, and out-of-sample results.

**Important disclosure**  
This is a hypothetical historical simulation for research and education. It is not investment advice and does not guarantee future performance. Historical results depend on data quality, fees, spreads, slippage, liquidity, corporate actions, and implementation assumptions.
