# Multi-Strategy Backtest Implementation Plan

**Goal:** Produce a reproducible five-year comparison of the current and proposed strategies.

1. Formalize date range, universe, risk, fill, fee, benchmark, and unavailable-data rules in the run artifacts.
2. Verify Alpaca CLI and authentication without printing credentials.
3. Fetch and fingerprint stock, BTC, option, quote, calendar, and corporate-action data through the CLI where supported.
4. Normalize timestamps and market sessions; reject look-ahead paths.
5. Implement a readable deterministic simulator with one strategy adapter per track.
6. Mark a strategy unavailable when required historical data or execution primitives are absent.
7. Generate trades, round trips, equity curves, metrics, benchmarks, warnings, and walk-forward splits.
8. Review artifacts and report Teaching Five metrics plus limitations.
