# AeroQuant — VRP Harvester

*Built by **Kraken Team** for the Alpaca AI Trading Agents Hackathon.*

Autonomous, multi-agent options trading system built for the **Alpaca AI Trading Agents Hackathon** (lablab.ai, Aug 28 – Sep 4, 2026, Options Alpha Agents track).

AeroQuant harvests the **Volatility Risk Premium (VRP)** — the well-documented tendency for options-implied volatility to run ahead of realized volatility — using defined-risk, non-directional structures (primarily Iron Condors) on European-style, cash-settled index options (XSP). A deterministic quantitative engine handles all math; a hierarchical multi-agent LLM layer (adversarial Bull/Bear debate, arbitrated by a Chief Strategy Agent) handles qualitative reasoning; a Python-only Risk Gate has final, non-bypassable authority over every order.

**Status:** pre-competition build. See [`AeroQuant-VRP-Harvester-PRD-2_6.md`](./AeroQuant-VRP-Harvester-PRD-2_6.md) for the full product/strategy spec and [`RUNBOOK-2_6.md`](./RUNBOOK-2_6.md) for the operational setup sequence.

## Pre-kickoff work disclosure

*(Required deliverable per the official hackathon FAQ — fill this in honestly before submission, don't leave it as a placeholder.)*

- Built before Aug 28, 2026: `<list what was actually built pre-kickoff — infra scaffolding, quant engine, risk gate, etc., per the Day 0 plan in PRD §11>`
- Built during the official window (Aug 28 – Sep 4): `<list what was built live during the event>`

## Why this architecture

- **The LLM never touches money directly.** Every trading agent (Volatility Analyst, Macro/News Analyst, Technical Manager, Bull/Bear Researchers, Chief Strategy Agent) can only *propose*. A deterministic, non-LLM Risk Gate is the sole path to Alpaca's Trading API.
- **The LLM never does math.** Historical Volatility, IV Rank/Percentile, Expected Move, skew, and position sizing are all computed by a plain Python/NumPy quant engine before any model is called.
- **Defined risk only.** Every position is a spread (Iron Condor / credit spread). Naked option selling is disallowed at the architecture level, not just by convention.
- **A known platform bug shapes the execution rules.** Alpaca's paper-trading environment has a confirmed, unresolved settlement bug for cash-settled index options — see PRD §9. Combined with an independently-confirmed fact about the scoring mechanism (settlement posts *overnight*, not same-day — PRD §11/§12), every position must be closed via an active limit order, never allowed to expire, and the account must be completely flat by **Thursday, Sep 3, market close** — a full day before the nominal Friday submission deadline.

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) — checkpointed `StateGraph`, not a chat-loop agent |
| LLM (primary) | Anthropic API — Claude Sonnet 5 (`strong_reasoning`/`critic`), Claude Haiku 4.5 (`fast_analysis`) |
| LLM (fallback) | Featherless.ai (Qwen3-32B) — confirmed hackathon tech partner; see [`model_gateway.py`](./model_gateway.py) |
| Broker | Alpaca Trading API + official [Alpaca MCP Server](https://github.com/alpacahq/alpaca-mcp-server) |
| Quant | Python, NumPy, Pandas — no LLM involved |
| Persistence | SQLite (WAL mode), account-scoped |
| API/dashboard | FastAPI (private, read-only) + a thin public frontend (Streamlit/Vercel — team's own choice, not a hackathon requirement) |
| Deployment | Single Linux VPS, systemd-supervised |

## Repository structure

```
agents/            # LLM-driven agent nodes (Volatility, Macro/News, Technical, Bull, Bear, Chief, Reflexion)
orchestrator/       # LangGraph StateGraph wiring, cycle scheduling
execution/          # Order Dispatcher, Broker Monitor, Exit Evaluator, Position lifecycle
data_engine/        # Alpaca MCP/SDK adapters, EvidenceEnvelope normalization
evaluation/         # Episodic/semantic memory, Reflexion critique, SQLite store
state/              # Runtime SQLite DB, ledger (gitignored)
reports/            # Per-cycle audit trail (gitignored)
tests/              # pytest suite + smoke tests
model_gateway.py    # Multi-provider LLM gateway with automatic failover (Anthropic → Featherless)
win_rate_validator.py  # Empirical SL/TP validation against real historical data
```

## Setup

Follow [`RUNBOOK-2_6.md`](./RUNBOOK-2_6.md) top to bottom — it's written as an executable sequence (VPS provisioning → credentials → smoke tests → deterministic core → LangGraph wiring → process supervision → account cutover → the Thursday-close deadline). Each section has a concrete pass/fail check; don't skip ahead.

Quick start for local development:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real credentials — see RUNBOOK §0 for what's needed and where to get it
python model_gateway.py       # confirms Anthropic + Featherless are both reachable
python win_rate_validator.py  # confirms current SL/TP calibration against real data (needs load_data() wired first)
pytest
```

## Key operational rules (see `CLAUDE.md` for the full non-negotiable list)

1. No LLM-generated content ever reaches Alpaca's order-submission endpoint directly — only the deterministic Risk Gate does.
2. Every option position is closed via an active limit order. None is ever allowed to expire naturally.
3. The account must be completely flat by **Thursday, Sep 3, 4:15 PM ET** — not Friday morning.
4. LLM API access is always a standalone, paid API key (Anthropic Console, Featherless dashboard). Never a Claude Code/ChatGPT-Plus subscription OAuth token, and never a third-party proxy claiming to route around official billing.

## Disclosures

This project trades exclusively in Alpaca's **paper-trading** environment — simulated funds, no real capital, no real financial risk. Nothing in this repository is investment advice. Options trading carries substantial risk and is not suitable for all investors.

## License

MIT — see [`LICENSE`](./LICENSE).