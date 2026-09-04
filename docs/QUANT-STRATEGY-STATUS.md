# Quant Strategy Status

## Runtime

- The primary runtime is a Windows laptop.
- LLM calls use the remote Featherless provider; Ollama embeddings are disabled.
- The quant engine remains deterministic and runs locally.
- Orders remain paper-only and must pass the deterministic risk gate.

## BTC Context

BTC/USD is measured on Alpaca crypto minute bars as optional shadow telemetry for
the equity momentum signal. It cannot create, override, or resize a trade.

- Normal BTC trend: recorded for later validation; it does not adjust sizing.
- Conflicting BTC trend: recorded as context while preserving the equity signal.
- Extreme BTC regime: recorded as a shadow regime only.
- BTC context never bypasses liquidity, risk, or execution checks.
- BTC data failure or staleness never creates a new trade signal.

## Strategy Capability

| Strategy | Current status | Reason |
|---|---|---|
| Directional long call/put | Executable | Supported by current single-leg options rail |
| Event volatility | Research candidate | Needs event-aware volatility structure and validation |
| American mispricing / relative value | Research candidate | Needs American-style valuation and paired-leg execution |
| Gamma scalping | Unsupported | Needs recurring underlying delta hedges |
| Put-call parity arbitrage | Unsupported | Needs stock leg, financing/dividend inputs, and synchronized execution |

Research candidates are reported for analysis but are not silently submitted by
the execution layer.
