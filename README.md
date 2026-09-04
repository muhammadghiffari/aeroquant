# AeroQuant — Autonomous Multi-Agent Options Trading System

> **Alpaca AI Trading Agents Hackathon** | lablab.ai | 28 Aug – 4 Sep 2026  
> **Branch:** `strategy/radith` | **Mode:** Paper Trading only

AeroQuant is an autonomous options trading system for **US Equity Options** built on the **Alpaca Trading API**. It combines a deterministic Quant Layer with an LLM Reasoning Layer (via Featherless API) in a hierarchical multi-agent pipeline — from market data ingestion all the way to paper trade execution.

---

## Architecture Overview

```
Alpaca Market Data API         Alpaca News API
        │                             │
        ▼                             │
  Quant Engine (non-LLM)             │
  IV Rank · Expected Move            │
  HV/IV Spread · Skew · PoP          │
  Trend Z-score                       │
        │                             │
        └────────────┬────────────────┘
                     │
         ┌─ Sub-Agents (LLM) ──────────────┐
         │  UnderlyingTrendAgent            │
         │  VolatilityAgent                 │
         │  NewsEarningsAgent               │
         └──────────────┬──────────────────┘
                        │
         ┌─ Managers (LLM) ────────────────┐
         │  TechnicalManager               │
         │  ContextManager                 │
         └──────────────┬──────────────────┘
                        │
         ┌─ Chief: StrategyDecisionAgent ──┐
         │  → picks candidate from Quant   │
         │    whitelist, or WAIT           │
         └──────────────┬──────────────────┘
                        │
         ┌─ RiskManagerAgent ──────────────┐
         │  → buying power · max loss      │
         │  · DTE · liquidity gate         │
         └──────────────┬──────────────────┘
                        │
              Execution (alpaca-py)
              place_option_order()
              Paper Trading ✓
```

**Design principle:** Quant runs first, LLM reasons second. No LLM ever calls another LLM — they run sequentially to conserve resources and stay auditable.

---

## Features

- 📊 **Quant Engine** — IV Rank, IV Percentile, HV/IV spread, Expected Move, skew, empirical PoP, trend Z-score; all computed with pure Python/NumPy/Pandas
- 🤖 **Multi-Agent LLM Pipeline** — hierarchical swarm (sub-agents → managers → chief → risk); each agent is stateless and schema-bound
- 🔒 **Hard Risk Gate** — Risk Manager Agent blocks every order that exceeds max-loss %, exposure limit, or liquidity threshold
- 📝 **Reasoning Trail** — every cycle saves a structured JSON report (`reports/`) for full transparency
- 🛡️ **Paper-only by default** — `ALPACA_PAPER_TRADE=True` is enforced; live trading requires an explicit opt-in
- 🔄 **Position Monitor** — deterministic TP/SL, critical-news reversal, and expiry safety runs separately from entry cycle
- 🌐 **MCP Server** — optional Alpaca MCP integration for agent tool-calling and interactive demos

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Exchange | [Alpaca Trading API](https://docs.alpaca.markets) (`alpaca-py`) |
| LLM Provider | [Featherless](https://featherless.ai) OpenAI-compatible API |
| LLM Models | `zai-org/GLM-5.2` (light) · `zai-org/GLM-5.3-Flash` (heavy) |
| Orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) |
| Data | Alpaca Market Data API — stock bars, option chain, Greeks, news |
| Serving | FastAPI + Uvicorn |
| Memory | LanceDB |
| Testing | pytest |

---

## Project Structure

```
aeroquant/
├── main.py                    # Entry point — trigger one pipeline cycle
├── config.py                  # All runtime parameters
├── server.py                  # FastAPI server
├── backtest_engine.py         # Backtesting module
├── runtime_safety.py          # Safety guards
├── data_engine/               # Alpaca API wrappers (stock, option, news)
├── quant_engine/              # IV Rank, PoP, Expected Move, trend Z-score
├── agents/                    # All LLM agents (base + 7 specialized)
├── orchestrator/              # LangGraph pipeline runner
├── execution/                 # Order executor, ledger, position manager
├── evaluation/                # Agent evaluation & memory
├── llm/                       # LLM client & provider abstraction
├── alerts.py                  # Telegram / notification alerts
├── scripts/                   # Utility & ops scripts
├── tests/                     # Pytest test suite
├── docs/                      # PRD, specs, plans, runbook
└── .env.example               # Environment variable template
```

---

## Quick Start

### 1. Prerequisites

```bash
git clone https://github.com/muhammadghiffari/aeroquant.git
cd aeroquant
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and fill in:
# ALPACA_API_KEY, ALPACA_SECRET_KEY  → Alpaca Paper Trading credentials
# FEATHERLESS_API_KEY                → Featherless API key
# ALPACA_PAPER_TRADE=true            → keep this True
```

### 3. Run a Single Analysis Cycle

```bash
python main.py --symbol AAPL --once
```

### 4. Run Periodically During Market Hours

```bash
python main.py --loop --interval 5
```

---

## Key Configuration

```python
# config.py — key parameters
WATCHLIST_SYMBOLS      = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT"]
IV_RANK_HIGH_THRESHOLD = 0.70   # above → lean premium-selling
IV_RANK_LOW_THRESHOLD  = 0.30   # below → lean premium-buying
MAX_LOSS_PCT_PER_TRADE = 0.03   # 3% buying power max loss per trade
MAX_DTE                = 45     # max days to expiry
MIN_DTE                = 7      # min days to expiry
CYCLE_INTERVAL_MIN     = 5      # run every 5 min during market hours
```

---

## Team

| Member | Branch |
|--------|--------|
| Muhammad Ghiffari | `strategy/ghiffari` |
| Radith | `strategy/radith` ← submission |
| Amil | `strategy/amil` |

---

## License

MIT License

Copyright (c) 2026 Muhammad Ghiffari, Radith, and AeroQuant contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

*AeroQuant v1.0 · Alpaca AI Trading Agents Hackathon 2026 · Paper Trading only*
