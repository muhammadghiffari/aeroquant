# CLAUDE.md

This file is read automatically at the start of every Claude Code session in this repo. It exists so context isn't re-explained every session and so settled decisions aren't accidentally re-litigated mid-edit. Full detail lives in [`AeroQuant-VRP-Harvester-PRD-2_6.md`](./AeroQuant-VRP-Harvester-PRD-2_6.md) (product/strategy) and [`RUNBOOK-2_6.md`](./RUNBOOK-2_6.md) (operational sequence) — **read the relevant PRD/Runbook section before making any non-trivial change**, don't guess from this summary alone.

## What this is

An autonomous multi-agent options trading bot for a live hackathon (Alpaca AI Trading Agents Hackathon, scored window Mon Aug 31 – Thu Sep 3, 2026, paper trading only). It sells defined-risk options spreads (mainly Iron Condors on XSP) to harvest the Volatility Risk Premium. A deterministic Python engine does all math and all risk enforcement; an LLM multi-agent layer (via LangGraph) does qualitative reasoning and proposes trades; nothing an LLM outputs can reach the broker without passing a non-bypassable Python Risk Gate first.

## Non-negotiable constraints

These are correctness/safety requirements, not style preferences. Do not relax, work around, or "helpfully" bypass any of these, even if a task seems to call for it:

1. **LLM agents never get trading tools.** No agent node should ever be given `bind_tools([...trading tools...])` or any Alpaca order-submission capability. Only the deterministic `Order Dispatcher` / `execution/` layer calls Alpaca's trading endpoints, and only after the Risk Gate approves.
2. **Every position is a defined-risk spread.** Naked option legs are never submitted. Multi-leg structures go through Alpaca's `MLEG` order type as a single atomic order — never "legged in" as separate individual orders.
3. **Limit orders only for options.** No market orders for options, ever (PRD §7). Compute the aggregate mid price from live bid/ask and pad ±5% toward the adverse side.
4. **No position may be allowed to expire naturally.** This is tied to a confirmed, unresolved Alpaca paper-trading settlement bug (PRD §9) *and* a separately-confirmed fact about how the scored equity snapshot works (settlement posts overnight, not same-day — PRD §11/§12). Every position must be closed via an explicit close order before its own expiry. This is enforced by a scheduled force-close job, not left to agent discretion.
5. **Hard deadline: zero open positions by Thursday, Sep 3, 4:15 PM ET.** Not Friday morning — see PRD §9/§11/§12 for why. Any scheduling logic, cron expression, or "last entry cutoff" constant should respect this, not the nominal Friday hackathon deadline. **If the team is running multiple parallel strategy accounts (see PRD §11b), this deadline applies to every account, not just whichever one ends up submitted** — the winning account isn't known until Friday morning, so a "flat" account that turns out to be the best performer must also be genuinely flat and settlement-clean.
6. **LLM API access is always a standalone, paid API key.** `ANTHROPIC_API_KEY` from `platform.claude.com`, `FEATHERLESS_API_KEY` from the Featherless dashboard, or an explicitly approved Anthropic-compatible paid provider endpoint. The `ANTHROPIC_BASE_URL` environment variable is governed as follows:
   - **Allowed:** official `https://api.anthropic.com` (default, always permitted without any `ANTHROPIC_BASE_URL` setting).
   - **Allowed:** explicitly approved Anthropic-compatible paid provider endpoints, listed here and in PRD §5.3.
   - **Prohibited:** personal-subscription proxies (Claude Code, ChatGPT-Plus, or similar OAuth tokens routing through unofficial endpoints), OAuth relay setups, and unverified or self-hosted endpoints.
   - **Approved as of 2026-08-31:** BluePack `https://ai.bluepack.my.id/anthropic` — adopted as the project's primary Anthropic-compatible provider per explicit team decision (see PRD §5.3).
   - Do not set `ANTHROPIC_BASE_URL` to any other endpoint without an explicit team decision documented in PRD §5.3.
7. **All persisted state is account-scoped.** Every DB row, log line, and report carries `alpaca_account_id`. The scratch/testing account and the official competition account must never share state, and the LangGraph checkpointer's `thread_id` must be `f"{alpaca_account_id}:{cycle_id}"`, not just `cycle_id`.
8. **An accepted-but-unfilled order is never recorded as an open position, and a submitted-but-unfilled close is never recorded as closed.** Only broker-confirmed fills/closes are authoritative. A crash-and-restart must resume cleanly without duplicating an order (idempotency key on every `OrderIntent`/`CloseIntent`).

## Architecture map

```
agents/            LLM-driven nodes: Volatility Analyst, Macro/News Analyst, Technical Manager,
                    Bull Researcher, Bear Researcher, Chief Strategy Agent, Reflexion Critic.
                    Each returns a typed Pydantic object via model_gateway.py — never raw text.
orchestrator/       LangGraph StateGraph: precheck -> evidence -> quant -> candidates -> memory
                    -> [volatility || macro] -> technical -> [bull || bear] -> chief
                    -> proposal_validator -> risk_gate -> persist_order_intent
execution/          Order Dispatcher, Broker Monitor, Exit Evaluator, force-close-before-expiry
                    scheduled job. This is where rule #4 above is actually enforced in code.
data_engine/        Alpaca MCP/SDK adapters. Normalizes everything into EvidenceEnvelope
                    (source, freshness, fallback_tier, quality_flags) before it reaches any agent.
evaluation/         Episodic + semantic memory (FinMem), Reflexion critique/rule-governance,
                    SQLite store.
model_gateway.py    Multi-provider LLM client: BluePack (Anthropic-compatible) primary,
                    Featherless (Qwen3-32B) fallback, circuit breaker per provider.
                    ANTHROPIC_BASE_URL=https://ai.bluepack.my.id/anthropic (adopted 2026-08-31).
                    Role policies: fast_analysis, strong_reasoning, critic — see PRD §5.3.
win_rate_validator.py   Empirical strike-placement/SL-TP validator against real historical
                    data. Run before trusting any §8 risk-parameter change.
```

## Conventions

- Python 3.11+. All inter-stage contracts (`EvidenceEnvelope`, `QuantReport`, `TradeProposal`, `RiskDecision`, `OrderIntent`, etc.) are Pydantic v2 models with an explicit `schema_version` field — never raw dicts crossing a plane boundary (Evidence → Reasoning → Safety/Execution → Learning).
- Money and quantities: decimal-safe representations, not raw floats.
- Timestamps: timezone-aware UTC everywhere in storage; convert to `America/New_York` only at the point of display/scheduling logic, via `zoneinfo` — never a manual UTC offset (DST matters across this event window).
- Every LLM call goes through `model_gateway.py`'s `ModelGateway.generate(role=..., policy=..., ...)` — never instantiate a raw `ChatAnthropic`/`ChatOpenAI` client inside an agent node.
- Tests: `pytest`. Deterministic components (quant engine, risk gate) should have unit tests requiring no network. Agent-graph tests should run against recorded fixtures where possible, not live API calls on every run.

## Current known gaps / open items

Don't assume these are resolved — check the PRD before writing code that depends on them:
- `model_gateway.py`'s Featherless fallback path has not yet been smoke-tested end-to-end against real credentials (RUNBOOK §8 covers this — run it before relying on it in a scored cycle).
- BytePlus Ark is present in some earlier drafts of `model_gateway.py` but is **not** an adopted provider (no partner/subscription evidence) — do not wire `BYTEPLUS_*` env vars back in without an explicit team decision (PRD §5.3/§12).
- Embedding provider for semantic memory retrieval is undecided (Anthropic's API doesn't serve embeddings).
- VPS geographic placement, Reflexion rule-activation threshold, and integration/release DRI are unassigned — see PRD §5.4/§12 for the full list.

## What NOT to do

- Don't "simplify" the Risk Gate into something an LLM agent can call directly, even behind a wrapper — the whole safety model depends on this boundary being structural, not conventional.
- Don't add a new LLM provider without checking PRD §5.3 first — provider choice here is tied to specific partner/ToS/cost evidence, not just "what works."
- Don't change the force-close deadline logic to target Friday instead of Thursday "to match the hackathon's nominal end date" — this was deliberately investigated and corrected; see PRD §9/§11.
- Don't remove the `include_raw=True` / structured-output validation path in `model_gateway.py` to "simplify" a call — malformed LLM output must fail loudly and trigger fallback, not be silently accepted.
