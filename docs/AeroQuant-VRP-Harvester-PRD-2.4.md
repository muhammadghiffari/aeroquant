# PRD — AeroQuant 2.4: VRP Harvester
**Autonomous AI Options Trading Agent — Alpaca AI Trading Agents Hackathon**

| | |
|---|---|
| **Event** | Alpaca AI Trading Agents Hackathon (lablab.ai) |
| **Track** | Options Alpha Agents |
| **Dates** | Aug 28 – Sep 4, 2026 (kickoff 15:00 UTC, submissions close Sep 4 15:00 UTC) |
| **Prize pool** | $6,000 (re-confirmed on kickoff day, Aug 28, 2026, against the live event page: 1 track, 0 tech partners, registration open. See §13.) |
| **Version note** | This is **2.4**. Changes from 2.3: §8 revises the stop-loss from 200% to 125% of credit, based on a verified 71,417-trade options-management backtest (projectfinance.com) reasoned specifically through §5.1's IV Rank>60 entry gate — not a blanket "tighter is better" claim (see §8, §13). §11 adds a Day-0 action item (a companion script, `win_rate_validator.py`) to empirically validate win-rate assumptions against real data before trusting any specific SL/TP calibration on its own. §12 documents two things for audit-trail completeness: (a) the event does not publish P&L-judging-snapshot timing mechanics, and the team's reasoned default — force-close everything before the deadline, unchanged from 2.3 — holds regardless; (b) a source (`vilkovgr/0dte-strategies`, a 0DTE working paper) that was investigated as a possible basis for upweighting the §5.1 skew signal and explicitly **rejected** after its own replication artifacts were found internally inconsistent under direct testing. §13 adds the projectfinance citation and documents the rejected source. **No change to §3's strategy matrix, §5.1's signal weighting, §7's execution rules, or the core architecture** — this revision touches risk-parameter calibration and documentation only. |

---

## 1. Executive Summary

AeroQuant 2.0 is an autonomous, multi-agent options trading system built for Alpaca's paper-trading environment. It harvests the **Volatility Risk Premium (VRP)** — the well-documented tendency for implied volatility to run ahead of realized volatility — using defined-risk, non-directional options structures on European-style, cash-settled index options (XSP). The system pairs a **deterministic quantitative engine** (no LLM math) with a **hierarchical multi-agent LLM reasoning layer**, a **layered memory architecture (FinMem)**, and **verbal reinforcement learning (Reflexion)** so the agent can adapt its own rules mid-competition without retraining any model weights.

The prior revision (2.1) added two things the original architecture did not account for: a **confirmed bug in Alpaca's paper-trading options settlement**, and the **specific, non-negotiable submission mechanics** of the lablab.ai hackathon. Both remain first-class product requirements below, not footnotes. Revision 2.2 (kickoff day) locked in a concrete LLM provider and orchestration decision — direct Anthropic API (Claude Sonnet 5 + Haiku 4.5) via a LangGraph-orchestrated checkpointed pipeline — and closed a macro-calendar gap (ISM Manufacturing PMI releasing the same minute as JOLTS on Sep 1). Revision 2.3 documented and closed off a rejected cost-optimization idea (routing agent LLM calls through a Claude Code subscription OAuth token). **This revision (2.4)** recalibrates §8's stop-loss based on a verified backtest source, adds an empirical validation step before kickoff, and documents two open-risk/audit items — see the Version note above and §5.3, §8, §11, §12, §13 below.

---

## 2. Goals & Success Metrics

The product's only real customer during evaluation week is the judging panel. This event's own Judging Criteria page confirms a P&L-led model, not lablab.ai's generic platform-wide 4-dimension template (Application of Technology / Presentation / Business Value / Originality). Each goal below is written against the confirmed dimension.

| Judging dimension | What it rewards | AeroQuant 2.0 commitment |
|---|---|---|
| **P&L Performance** *(visually the lead criterion on the event page)* | Trading performance of the submitted agent in the Alpaca paper trading environment — actual P&L and how effectively the strategy performs through its trading activity | This is why §9's settlement-bug guardrail and §11's "agent should already be mature before kickoff" plan both exist: a strategy that's still being debugged on Day 3 has fewer independent trade cycles to show real, defensible P&L by Sep 4 |
| **Technology Implementation** | How effectively the project uses Alpaca's Trading API, MCP server, CLI, and other required tech to build an autonomous agent | Hybrid Intelligence split (deterministic engine vs. LLM reasoning); full use of Alpaca's Trading API, MCP Server (§7), and options data |
| **Creativity & Originality** | Originality of the concept, trading strategy, agent behavior, and overall approach | Adversarial Bull/Bear debate + Reflexion-driven semantic memory, rather than a single-prompt trading bot |
| **Presentation & Execution** | Clarity of communication, demonstrating the agent in action, and explaining the reasoning behind strategy and results | 5-minute video and slide deck planned and drafted well before Day 7; demo shows the agent's actual reasoning trail, not just a final number |
| **Social Engagement** *(bonus, not core)* | Both content quality and the engagement it generates (likes, comments, shares) | Treat as a low-effort bonus layered on top of the core four — don't let it compete for time against P&L Performance |

Internal engineering success metric: **verifiable, risk-adjusted P&L** over the 5–7 trading days, with zero unhandled exceptions, zero naked/undefined-risk positions, and zero reliance on unverified paper-account settlement math (see §9).

---

## 3. Strategic Foundation: Harvesting the Volatility Risk Premium

The Volatility Risk Premium is the persistent gap between an option's Implied Volatility (IV) — the market's forward-looking price for insurance — and the Realized Volatility (RV) that the underlying actually delivers. Because buyers of options (portfolio insurance, hedgers) are structurally willing to overpay, IV exceeds RV in roughly **83–87% of rolling 30-day windows** on the S&P 500 going back to 1990.

Given a 7-day evaluation window, the agent should **not** attempt to forecast direction. The strategic mandate is a **non-directional or mildly directional short-premium book**, harvesting Theta and Vega rather than guessing Delta.

**Core strategies:**
- **Iron Condor** — sell an OTM put spread and an OTM call spread simultaneously; the **dominant, default play** across the week, used whenever the regime is neutral-to-ambiguous.
- **Bull Put Spread / Bear Call Spread** — directional credit spreads, used only as a narrow, regime-conditional override when the Technical Manager (§5.2) detects a genuine mild trend alongside high IV. Treated as the minority of trades, not a co-equal alternative to test in parallel.
- **Debit spreads and any other long-premium, direction-betting structure are explicitly excluded from the core matrix.** They contradict the VRP thesis (selling rich premium, not buying it), and no source reviewed for this PRD — academic or institutional — supports a directional long-premium approach as reliable over a 5-day window. If ever revisited, it would be a clearly-labeled minor/optional addition, never part of the core harvesting logic.
- **Naked option selling is explicitly disallowed** — infinite tail risk is incompatible with automated risk management. Every position must be a defined-risk spread.

**Why Iron Condor is the structural default, not one option among several to A/B-test live:** a CBOE-commissioned academic study (Black & Szado, 2016) compared six SPX options-selling benchmark indexes — BuyWrite (BXM), PutWrite (PUT), Iron Butterfly (BFLY), 30-Delta BuyWrite (BXMD), Covered Combo (CMBO), and Iron Condor (CNDR) — over 29.5 years of monthly data (mid-1986–end of 2015). The Iron Condor index (CNDR) posted the **lowest annualized volatility (7.23%)** of the group, and a far lower incidence of large-loss months (10 months worse than −6% over 29.5 years) than either the S&P 500 itself (15 months) or single-sided PutWrite. Iron Butterfly (BFLY) had the best tail-risk profile of all (2 months), at the cost of a narrower profit zone. Neither BXMD nor PUT — the two highest-*return* benchmarks in the study — matches CNDR's drawdown control. This is strategy-type evidence, not DTE/strike-specific evidence (CNDR is a monthly-cadence benchmark, not 1–5 DTE), but it directly supports the choice of Iron Condor as the risk-adjusted default for a short, high-stakes, judged window, where a single bad tail event is far more damaging to the demo than a slightly lower average return.

**Why the matrix is a pre-set policy, not a live experiment:** the four-strategy selection matrix is a single conditional decision rule, fixed *before* Aug 28 based on the research above — it is deliberately **not** an approach where all strategy types are run in parallel during the competition week to see which "wins." With an effective live window of ~4.5 trading days (§11), splitting trade count across multiple strategy types would leave each with too few completed cycles for any performance difference to be statistically meaningful — any apparent "winner" would most likely reflect noise, not edge, and risks an incoherent story for judges (a system that looks like it's still experimenting, not one that already knows what works). The research is used to fix the policy in advance; the live week is for executing it, not for re-deriving it.

---

## 4. Instrument Selection: Why XSP, Not SPY or SPX

Standard equity/ETF options (SPY, QQQ) are **American-style**: the buyer can exercise at any time, which exposes a short-premium book to early assignment, dividend risk, and overnight pin risk — a short call could convert into an unhedged short-stock position while the system is unattended.

**European-style, cash-settled index options (SPX/XSP)** eliminate that failure mode entirely:
- **Cash settlement** — no physical delivery, no accidental equity accumulation.
- **No early exercise** — mathematically restricted to expiration only, so the agent can hold short premium into the steepest part of the Theta curve without assignment risk.

| Instrument | Notional per contract | Fit for a $100k paper account |
|---|---|---|
| SPX | ~$600,000 | Too large for precise position sizing |
| **XSP** | **~$60,000** (1/10 multiplier) | **Sweet spot** — SPY-like granularity with SPX-like structural safety |

**Decision: anchor the entire strategy to XSP.** This removes an entire class of assignment-management bugs and lets engineering effort focus on signal generation and execution logic — with one important caveat (§9).

---

## 5. System Architecture: Hybrid Intelligence

**Core design principle:** *the LLM must never be trusted to calculate a number, and the deterministic engine must never be trusted to interpret qualitative nuance.* LLMs are well-documented to hallucinate on precise numerical/statistical tasks, so all pricing math is isolated from the reasoning layer.

### 5.1 Deterministic Quantitative Engine (Python / NumPy / Pandas, non-LLM)

Computes the following before any LLM call is made:

- **Historical Volatility (HV30)** — 30-day rolling log-return volatility, annualized: `HV30 = std(ln(Pt/Pt-1)) × √252`
- **IV Rank & IV Percentile** — where current IV sits versus its own recent range and history; a locally-built rolling IV history is required since brokerage APIs typically don't retain deep IV archives. Both exceeding **60** is the strongest quantitative mandate to authorize short-premium strategies. Since the event's Account Requirements explicitly allow using any paper account to prototype and test before submission (§10), start a lightweight cron snapshotting XSP ATM IV every 15–30 min **as early as possible before Aug 28**, not on Day 0/Day 1 — every extra day of lead time is a real day of history instead of a synthetic one. Until the local history has enough depth to make IV Rank/Percentile meaningful (roughly 10+ trading sessions), treat the metric as low-confidence and require corroboration from VIX/VIX9D term structure (via the MCP server's `index-data` toolset — add it to `ALPACA_TOOLSETS` alongside the set in §7) before authorizing a short-premium trade on that signal alone.
- **Expected Move (EM)** — the ATM straddle cost, expressed as a % of spot: `EM% = (Mid ATM Call + Mid ATM Put) / Spot × 100`. This directly dictates where the Chief Strategy Agent is allowed to place short strikes.
- **Volatility Skew (25-delta)** — `Skew = IV(Put,25Δ) − IV(Call,25Δ)`. A steep positive skew (fear priced into puts) slants selection toward Bull Put Spreads over neutral Iron Condors. **(Unchanged in 2.4 — see §12 for a source that was investigated as a basis for upweighting this signal and explicitly rejected; this signal's weighting remains as specified since 2.1: a minor tiebreaker, not a co-equal input to IV Rank/Percentile.)**
- **Momentum Z-Score** — 20-day normalized price momentum, giving the LLM a single standardized signal for trend classification instead of raw moving averages.

### 5.2 Hierarchical Multi-Agent System

Modeled on the published "TradingAgents" multi-agent literature: specialized personas outperform a single monolithic LLM doing the entire pipeline, because single agents lose context and conflate concerns.

| Agent | Substrate | Role |
|---|---|---|
| Data Gatherer | Python / API script | Pulls Alpaca market data, options chains, news |
| Quant Engine | Python / NumPy | Computes §5.1 metrics — no LLM involved |
| Volatility Analyst | Claude Haiku 4.5 (Anthropic API) | Classifies the volatility regime (e.g. "High IV, short-premium bias") — cheap, fast, classification-style task, no deep multi-step reasoning needed |
| Macro/News Analyst | Claude Haiku 4.5 (Anthropic API) | Reads Alpaca News API; acts as a qualitative circuit breaker ahead of CPI, FOMC, and other scheduled macro events — same cost/latency profile as Volatility Analyst |
| Technical Manager | Claude Sonnet 5 (Anthropic API) | Synthesizes Volatility + Macro reports into directional conviction |
| **Bull Researcher / Bear Researcher** | Claude Sonnet 5 (Anthropic API), adversarial pair | Bull argues the strongest case for the premium harvest; Bear ruthlessly attacks it, flagging skew anomalies and event risk. This debate is what reduces single-path hallucination and forces explicit consideration of tail risk. |
| Chief Strategy Agent | Claude Sonnet 5 (Anthropic API), forced tool-use / structured output | Arbitrates the debate, selects one strategy, and must specify exact strikes/expirations from a pre-validated liquid-contracts list — structurally preventing hallucinated orders. Runs via a direct API call with a forced JSON schema (`tool_choice`), never via a conversational chat-loop — see §5.3. |
| **Risk Manager** | **Python, deterministic, non-bypassable** | Final gate. Rejects any proposal that violates hard risk limits, logs the rejection, and halts the cycle. LLM discretion ends here. |

### 5.3 LLM Provider & Orchestration Decision (added in 2.2; re-confirmed 2.3)

**Decision: direct Anthropic API (Claude Sonnet 5 + Claude Haiku 4.5), orchestrated by LangGraph — not a third-party gateway model, not a chat-loop CLI agent.**

**Provider — why Claude direct, not a routed/gateway model:**
- Anthropic's own pricing page confirms Claude Sonnet 5's **$2/$10 per million input/output tokens is now the standard, permanent price** — the previously scheduled Sep 1, 2026 increase to $3/$15 has been cancelled (`platform.claude.com/docs/en/about-claude/pricing`, checked same-day as this revision). Claude Haiku 4.5 is $1/$5 per million tokens.
- **Cost math against the ~4.5 trading-day live window:** ~26 cycles/day × 4.5 days ≈ 117 cycles, 6 LLM calls per cycle (Volatility, Macro, Technical, Bull, Bear, Chief) plus Reflexion on each close ≈ 750 calls total for the week. At a generous ~2.5K input / ~450 output tokens per call, split Haiku (Volatility + Macro, 2 of 6 calls/cycle) / Sonnet (the other 4): **total API spend for the full week comes in under $10** — inside the existing self-funded ~$10-15/week budget (§13/§10) with room to spare, even before prompt caching (90% off repeated system-prompt/evidence-context reads) is applied.
- **Reliability:** Claude models' `structured_outputs` capability is a first-class, documented field on every current model (confirmed via the Models API, `platform.claude.com/docs/en/api/models/list`) — directly addresses the Priority-1 "invalid JSON from Chief Strategy Agent" risk this PRD already flags, without needing a beta-labeled structured-output mode or a translation layer.
- **Auth:** a **standalone Anthropic API key** created in the Console (`platform.claude.com`), billed per-token, entirely separate from any Claude subscription plan — this is the officially sanctioned path for "developers building products or services that interact with Claude's capabilities" (`code.claude.com/docs/en/legal-and-compliance`).

**Orchestration — LangGraph, not a chat-CLI agent:**
- The 7-role pipeline (Data Gatherer → Quant Engine → Volatility/Macro/Technical → Bull/Bear → Chief → Risk Gate) with hard plane boundaries and durable checkpoints is exactly the "stateful graph with checkpointing, durable execution, conditional edges, human-in-the-loop" pattern that 2026 framework comparisons consistently name LangGraph for — model-agnostic, calls Anthropic natively via `ChatAnthropic` with no compatibility-shim needed, and checkpoints survive process restarts (directly closes the Priority-0 "broker side effects lack durable checkpoints" and "state is not account-scoped" findings).
- A vendor-native option (Anthropic's own Claude Agent SDK) was considered and **rejected for the core trading loop**: it is explicitly Claude-only by design (*"the SDK is designed for Claude models specifically... this is an architectural constraint to evaluate before committing"*) and is built around a single well-tooled agent, not a multi-role checkpointed graph — a mismatch for this exact architecture, independent of which model provider is chosen.
- A lightweight external cron (systemd timer or equivalent) still fires the 15-30 min cycle trigger; LangGraph owns everything from that trigger through checkpointed completion. This replaces any chat-loop CLI agent (e.g. Hermes) as the decision-path orchestrator — such tools remain fine for auxiliary notifications/monitoring, but never sit on the path that produces the Chief's JSON order proposal.

**Explicitly rejected (raised and closed out 2.3): routing agent LLM calls through a Claude Code subscription OAuth token instead of a standalone API key.** The idea — pointing a tool like Hermes at a `CLAUDE_CODE_OAUTH_TOKEN` from a Free/Pro/Max plan to draw on subscription quota instead of paying per-token — is not viable, for two independent reasons:
- **Policy:** Anthropic's Claude Code legal and compliance documentation states plainly that OAuth authentication "is intended exclusively for Claude Code and claude.ai," and that using those tokens "in any other product, tool, or service... is not permitted and constitutes a violation of the Consumer Terms of Service."
- **Technical enforcement:** subscription OAuth tokens presented outside Claude Code/claude.ai have been rejected at the API layer since January 2026, with credible reports of account-level restriction for attempting it.
- **No upside to weigh against that risk:** the cost math above already puts the full week's legitimate API spend under $10. **Decision: standalone Anthropic API key only, for both development (Claude Code) and the production trading loop (Claude Platform API).**

---

## 6. Memory & Adaptation: FinMem + Reflexion

Traditional RL (policy gradients, FinRL-style Q-learning) needs millions of episodes to converge and breaks down under non-stationary markets — completely impractical in a 7-day window. AeroQuant 2.0 instead uses **verbal reinforcement learning**, adapting behavior through natural-language rules rather than weight updates.

**Layered memory (FinMem):**
1. **Working memory** — the live scratchpad for the current decision cycle (price feeds, IV Rank, breaking news, debate transcript). Clears after each cycle.
2. **Episodic memory** — an immutable log of every trade proposed, executed, or closed, with full reasoning and realized P&L, queryable by similarity search (e.g. "all Iron Condors opened when IV Rank > 70 within 48h of CPI").
3. **Semantic memory** — generalized rules distilled from episodic history, injected directly into the agent's system prompt (e.g. "Never execute 0DTE options in the first 30 minutes of the session").

**Reflexion loop**, triggered whenever a position closes:
1. **Outcome ingestion** — original trade thesis + realized P&L from the Alpaca execution ledger.
2. **Root-cause critique** — the agent distinguishes flawed logic, genuine macro shock, or ordinary statistical variance.
3. **Actionable refinement** — generic statements ("trade more carefully") are rejected; the agent must produce a specific rule change with a concrete trigger condition.
4. **Memory injection** — the new rule becomes a binding constraint for every future cycle.

---

## 7. Execution Infrastructure: Alpaca MCP Server

- Use the **official Alpaca MCP Server**, which per Alpaca's own docs (docs.alpaca.markets/us/docs/alpaca-mcp-server) currently exposes **65 tools across 11 toolsets**: `account`, `trading`, `watchlists`, `assets`, `stock-data`, `crypto-data`, `options-data`, `corporate-actions`, `news`, `fixed-income-data`, `index-data`.
- **Scope with `ALPACA_TOOLSETS`** restricted to `account, trading, assets, options-data, stock-data, index-data`. `get_option_contracts` — the tool the Chief Strategy Agent needs to pull strikes/expirations for every Iron Condor or credit spread — lives in the `assets` toolset, not `options-data`. Without it scoped in, the agent would have market data but no way to actually enumerate tradable contracts. `index-data` supports the VIX/VIX9D cross-check described in §5.1. This is the structural defense against tool hallucination — if the agent tries to reach for crypto or fixed-income tools it has no business touching, the server simply doesn't expose them.
- **Multi-leg orders only.** Iron Condors and credit spreads must be submitted as a single `MLEG` order (an array of `legs[]`, each with `ratio_qty` and `position_intent`) so the spread fills atomically. Legging in individually is explicitly disallowed — a price gap mid-fill could leave the agent holding a naked, infinite-risk leg.
- **Limit orders only, no market orders for options.** Pull live bid/ask per leg, compute the aggregate mid price, and pad ±5% toward the adverse side for the final limit price.

---

## 8. Risk Management Framework

The Risk Manager (§5.2) is the ultimate arbiter — not the agent's intelligence.

| Parameter | Rule | Rationale |
|---|---|---|
| **Risk-adjusted sizing** | Max loss per trade ≤ a **fractional-Kelly base %** of dynamic equity, **scaled down as VIX9D percentile rises** (smaller size in elevated-vol regimes, full size in calm regimes) | Static flat-% sizing ignores that absolute vol level — not just the IV Rank entry filter — is itself a predictor of tail risk; scaling size to it is a second, independent risk control layered on top of the entry filter, not a replacement for it |
| Spread width | Max loss computed exactly from width − net credit | Risk is calculated, never assumed |
| Liquidity guardrail | Bid-ask spread capped as a % of mid-price | Avoids slippage-inflated mark-to-market losses in illiquid strikes |
| Max exposure | Capped % of buying power; max 6 concurrent positions | Prevents over-leverage and correlated blowups |

**On the sizing upgrade above — scope of the source, stated explicitly to avoid overclaiming:** the VIX9D-scaled Kelly approach is grounded in Wysocki (2025, arXiv 2508.16598), an empirical study of systematic **short put-writing** on S&P 500 index options. Two things transfer cleanly from that paper and one does not:
- **Transfers directly:** VIX9D outperforms VIX30D as a sizing signal for short-dated strategies, and position sizing is a larger driver of long-run risk-adjusted performance than strike/DTE choice. Both are architecture-level findings, independent of whether the position is a naked put or a defined-risk spread.
- **Does not transfer directly:** the paper's specific numeric recommendation (far-OTM 5–10%, 0–1 DTE) was optimized for **naked short puts**, where the downside is open all the way to the strike. AeroQuant's Iron Condor/credit-spread legs are already capped by a long wing, which changes the risk/reward geometry of moneyness and DTE choice. **The existing 1–5 day DTE band and strike-selection logic (driven by Expected Move, §5.1) are retained as-is** — they are not replaced by Wysocki's specific numbers. Only the *sizing multiplier concept* is adopted from this source.
- **This entry filter (IV Rank/Percentile > 60, §5.1) and this sizing scale (VIX9D percentile) are deliberately two separate risk dimensions**, not duplicates of each other: the entry filter answers "is premium rich enough, relative to its own recent history, to sell right now"; the sizing scale answers "how much capital to risk given the current absolute volatility regime." A day can pass the first test while still warranting smaller size on the second.

**Compressed-window tactics:**
- **Take-profit automation at 50%** of max credit received; **stop-loss revised to 125%** of initial credit (down from 200% in 2.1–2.3). Capital recycles immediately into the next setup rather than waiting for pennies of remaining theta. **Rationale (2.4):** the 50%/200% pairing used through 2.3 implies a breakeven win rate of `p = SL/(SL+TP) = 2.00/(2.00+0.50) = 80%` — a demanding threshold for a defined-risk spread with only a handful of completed cycles to average over. A 71,417-trade SPY iron condor management backtest (projectfinance.com, 2007–2017; see §13) found that tighter stop-losses specifically outperformed looser ones in **high-IV-at-entry** buckets, while looser stops performed better in low-IV entries — it is not a blanket "tighter is always better" finding. Since §5.1's IV Rank/Percentile > 60 gate means every AeroQuant entry is, by construction, a high-IV entry, the source's high-IV finding is the one directly applicable here, not its low-IV finding or its exact percentages (the source is 45 DTE — 30 to 60 day expiration cycles — not DTE-matched to AeroQuant's 1–5 day band). 125% brings the breakeven win rate down to `2/(2+0.5)`→`1.25/(1.25+0.5) = 71.4%`, a more plausible target for near-ATM 1–5 DTE defined-risk spreads. **This is a reasoned starting point, not a proven-optimal number for this exact DTE band** — §11 adds a Day-0 action item to validate it empirically before kickoff.
- **DTE band: 1–5 days.** 0DTE offers the fastest decay but carries extreme intraday gamma risk (especially in the opening/closing 30 minutes) that can trigger noise-driven stop-outs — avoid it as the primary band.
- **Kill switches:** halt all new entries if daily realized P&L breaches **-3% of equity**, or if the Risk Manager rejects **5 consecutive proposals**. This is what keeps one bad regime shift from cascading into an unrecoverable drawdown.

---

## 9. ⚠️ Known Platform Risk: Confirmed Paper-Settlement Bug — MANDATORY GUARDRAIL

**Status as of Aug 26, 2026: confirmed by Alpaca, unresolved, no fix ETA.**

An independent check against Alpaca's own community forum confirms a real, currently-open bug in **paper-trading** index-options settlement (thread: *"Paper trading bugs with index option settlement,"* forum.alpaca.markets, posted Aug 3, 2026):

- Running a 0DTE XSP iron butterfly over four sessions (Jul 28–31, 2026), a paper account showed **+$8,294** against a correct P&L of **−$1,437** — roughly a **$9,700 error on a $100k account in four trading days**.
- Two distinct root causes were documented: (1) out-of-the-money short legs are credited cash instead of being floored to zero at settlement, and (2) the settlement index value used doesn't match the official S&P 500 close on the affected days.
- **Alpaca staff (`grace_alpaca`) confirmed the bug on Aug 6, 2026**, stating it is isolated to the paper-trading environment and is being worked on, with **no ETA given**.
- Cross-checked against Alpaca's public API changelog (docs.alpaca.markets/us/changelog) through its latest entries as of **Aug 28, 2026**: **no entry references a fix for options settlement.** Treat this as still open until proven otherwise, and re-check the changelog periodically through the week.

**Impact:** for any defined-risk structure (condor, butterfly, vertical), the paper account's reported P&L is **not trustworthy** if a short leg is allowed to expire naturally OTM. The account can drift upward regardless of what the market actually did — which is exactly the kind of artifact that would be catastrophic to report to judges as "verified P&L."

**Mandatory product requirement (non-optional, highest priority):**
1. **Force-close every short leg before expiry** — never let a position settle naturally through the paper expiration process. The few extra basis points of theta captured in the final minutes are not worth the risk of reporting fictitious P&L.
2. **Daily manual verification routine**, especially at kickoff: open one small OTM short position, let it expire, and manually compare Alpaca's reported settlement cash flow against the correct intrinsic-value calculation. Re-run this check periodically through the week — the bug's status can change without notice.
3. Log this as an explicit assumption/limitation in the submission's pitch deck. If judges ask about P&L verifiability, "we identified and guarded against a live platform bug" is a legitimate technical-depth story — silence on it is a reputational risk.

---

## 10. Hackathon Operational Compliance

Verified directly against this event's own live page — About, Challenge, Account Requirements, Judging Criteria, Event Schedule, and What to Submit sections — plus lablab.ai's general hackathon rule book.

**Core challenge requirements (hard gates, confirmed on the event page, not just implied by the strategy design):**
- **Autonomous agents** — participants must build autonomous AI trading agents using Alpaca's Trading API.
- **MCP or CLI** — the project must utilize either Alpaca's MCP server or its CLI tools.
- **Options trading** — *all* strategies must incorporate options trading. AeroQuant 2.0's XSP-only design satisfies this by construction, but it's worth stating explicitly: a strategy that drifts toward pure equity/ETF trades mid-week would fail this gate.

**Account requirements:**
- **Explore freely during development:** sign up for Alpaca and open a paper trading account to explore the API, MCP server, and CLI, prototype the agent, and test strategies. **Any paper account can be used during development** — this is explicit, first-party confirmation that pre-kickoff building is fine (see the Day 0/Day 1 plan in §11).
- **Required for judging:** the final submission must run on a **brand-new, dedicated Alpaca paper trading account created for this hackathon**. *"Projects run on an existing or reused account will not be eligible for judging"* — this is a hard eligibility gate, not a scoring deduction.
- **Competition account starting balance must be set to exactly $100,000.**
- **One-page write-up required**, covering the AI logic, risk gates, and Alpaca infrastructure implementation. Added to the deliverables checklist below and to §11's Day 0 prep list.

**Mandatory deliverables (confirmed against the event page's own "What to Submit" section):**
- [ ] **Basic info:** project title, short description, long description, technology & category tags
- [ ] **Cover image**
- [ ] Pitch video, **≤ 5 minutes, MP4**
- [ ] Slide deck, **PDF**
- [ ] **Public GitHub repository**
- [ ] Demo application **hosted on Streamlit, Replit, or Vercel** — a specific, mandatory platform constraint, not a suggestion. If the current deployment plan doesn't target one of these three, add it to the infra/deployment plan now.
- [ ] **Application URL** live and reachable
- [ ] Submission is **original work, open source, and MIT-License-compliant** unless stated otherwise. Audit any finance/quant dependency before adding it — a handful of common Python finance libraries carry GPL or proprietary terms, and a late-discovered license conflict is a disqualification risk, not a warning.
- [ ] **A new, dedicated Alpaca paper-trading account created specifically for this hackathon, starting balance $100,000, with its Alpaca paper account ID included in the submission.** Never trade anything else on it — a mixed-use or pre-kickoff-traded account fails the eligibility gate above outright, not just the "verifiable P&L" story.
- [ ] **One-page write-up:** AI logic, risk gates, Alpaca infrastructure implementation.
- [ ] **(Optional) up to 5 social media post links** — see Build in Public below.

**"Build in Public" — social engagement mechanics:**
- Share progress publicly on **X and LinkedIn** while building — process, reasoning, and setbacks, not just the final result.
- Tag both partners in every post: lablab.ai (X `@lablabai`, LinkedIn `lablab.ai`) and Alpaca (X `@AlpacaHQ`, LinkedIn `Alpaca`).
- Submit **up to 5** post links with the final submission. Judged on both content quality and the engagement generated (likes/comments/shares) — still a bonus layered on the four core criteria, so budget minimal time here, but 3–5 genuine posts across the week costs little and is free upside.

**Judging criteria — write explicit narrative for each:**
- **P&L Performance** — the trading performance of the submitted agent on the fresh paper account: actual P&L and how the strategy performs through its trading activity. **Visually the lead criterion on the event page** — treat it as the one that has to be unambiguously good, not merely "technically correct."
- **Technology Implementation** — how effectively the project uses Alpaca's Trading API, MCP server, CLI, and other required tech. The Hybrid Intelligence split and full MCP/Trading API integration cover this well already.
- **Creativity & Originality** — originality of the concept, strategy, agent behavior, and overall approach. Lead with the adversarial Bull/Bear debate and Reflexion-driven semantic memory as the differentiator versus a single-prompt trading agent.
- **Presentation & Execution** — clarity of communication, demonstrating the agent in action, and explaining the reasoning behind the strategy and results. Script and storyboard the video/deck well before the deadline; show the actual reasoning trail, not just a P&L screenshot.
- **Social Engagement (bonus)** — quality of content and the engagement it generates (likes, comments, shares). Don't spend meaningful time here; it's explicitly a bonus layered on the four core criteria, not a fifth equal-weight one.

**To watch before kickoff:** the event page lists **Technology Partners as "to be announced... before the kickoff,"** with a note that partner-specific prizes require that partner's technology to actually be integrated into the submission. Re-check the event page at or just before Aug 28 — if an announced partner technology fits naturally into the existing stack, it's free additional prize-pool upside; don't force-fit one that doesn't belong.

**Operational runbook addition:** check **status.alpaca.markets every morning** during the competition week. If the agent fails to submit orders with no obvious code-side cause, check the status page **before** assuming it's a bug in your own system — misdiagnosing a platform-side issue as an agent bug wastes limited hackathon time.

**Build-window compliance.** This event's own Account Requirements copy states: *"use any paper account you like during development."* That's first-party, event-specific confirmation that building and maturing the full agent — including the LLM reasoning loop — before Aug 28 is fine, overriding lablab.ai's more cautious generic platform FAQ (which says only "core AI functionality" needs to be built during the event window). The only hard constraint is the account-freshness gate above. §11 reflects this directly.

---

## 11. 7-Day Execution Timeline

**Corrected trading calendar:**
- **Fri Aug 28 (kickoff, 15:00 UTC = 11:00 AM ET):** the exchange has already been open since 9:30 AM ET — the agent only gets the last **~5 hours of RTH** that day (11:00 AM–4:15 PM ET). The event page's own schedule shows kickoff isn't a single instant but a ~1-hour opening program: Kick-off 22:00 WIB (11:00 AM ET) → lablab.ai opening words 22:05 WIB → Alpaca opening words 22:10 WIB → Introduction to the Challenge 22:15 WIB → Hackathon Guide 22:25 WIB → Discord Q&A 23:00 WIB (12:00 PM ET). If the team watches this live, the realistic first live-trading window is closer to **~4 hours (12:00–4:15 PM ET)**, not the full 5 — plan the first multi-agent cycle for right after the Q&A, not exactly at kick-off.
- **Sat–Sun Aug 29–30: exchange closed, full stop.** Cboe's Global Trading Hours (GTH) session for SPX/VIX/XSP runs 8:15 PM–9:15 AM ET, Sunday night through Friday morning — but the last GTH session before the weekend ends with Friday's Curb close (4:15–5:00 PM ET), and the next session doesn't open until **Sunday 8:15 PM ET**. That's a **~51-hour dead zone** with zero trading possible on any calendar, RTH or GTH.
- **Mon–Thu Aug 31–Sep 3: 4 full RTH days (9:30 AM–4:15 PM ET).** This is the actual engine of the competition week.
- **Fri Sep 4 (15:00 UTC = 11:00 AM ET deadline):** exchange opens 9:30 AM ET, but only **~1.5 hours** exist before submission closes.
- **Event risk on the close-out morning:** the Employment Situation (Nonfarm Payrolls) report — the only high-importance macro release scheduled inside the competition week — drops at **8:30 AM ET on Sep 4, one hour before the exchange opens.** A surprise print can produce a volatile gap-open exactly in the window the agent needs to force-close cleanly.

**P&L-maximization implication (P&L Performance is the lead judging criterion — §10):** don't open new positions the morning of Sep 4 — with only ~1.5 hours left, any position still open at submission is unrealized mark-to-market, not verifiable P&L, which directly conflicts with §9's own force-close mandate. Instead, concentrate trade cycles in the 4 full days (Aug 31–Sep 3), leaning toward the short end of the §8 DTE band (1–2 days) to complete more open→close cycles inside the narrow window — every completed cycle is realized P&L a judge can verify, not just potential. **This same logic extends past the deadline too — see §12's note on P&L-judging timing: force-close and stop, don't leave positions open hoping the account looks better whenever judges happen to check it.**

**Day 0 (Thu Aug 27, and earlier if possible — pre-kickoff dev, confirmed fine per §10):** use every available day, not just Aug 27, to get the agent genuinely mature before the fresh account goes live. Because the judging panel scores **live paper-trading execution during competition week, not historical backtest quality** (§10), and the multi-agent LLM + Reflexion loop is non-deterministic and effectively unbacktestable in the traditional sense, Day 0's real job is **forward-testing and stress-testing the infrastructure**, not backtesting the strategy:
- **Start the IV snapshot cron now (§5.1)** — this is the single highest-leverage pre-kickoff task, since every extra day of lead time is a real day of IV Rank/Percentile history instead of a synthetic one built from scratch on Day 1.
- Repo skeleton, dependencies, CI, and the Streamlit/Replit/Vercel deploy target wired end-to-end.
- Deterministic Quant Engine (§5.1) and Risk Manager (§8) written and unit-tested.
- MCP server + `ALPACA_TOOLSETS` (§7) validated end-to-end, including the full multi-agent LLM pipeline (Data Gatherer → Volatility/Macro Analysts → Bull/Bear debate → Chief Strategy Agent) and FinMem/Reflexion loop — **build and mature the whole agent now**, not just the deterministic pieces. Run it against a throwaway/scratch Alpaca paper account so it's genuinely trading (not just unit-tested) before Aug 28.
- **Forward-test on the scratch account, not just unit tests.** Confirm three things specifically: (1) the Quant Engine pulls data without erroring, (2) an `MLEG` Iron Condor order fills without a reject from Alpaca's API, (3) the Risk Manager actually blocks a trade proposal that breaches a hard limit.
- **Isolated quantitative validation.** Pull ~1 year of VIX history free and official directly from Cboe and ~1 year of SPX (`^GSPC`) from Yahoo Finance's free CSV export. Run HV30/IV Rank/EM% against this dataset and cross-check the output manually against **thinkorswim's Guest Pass** (Schwab — free, no funded account required, available internationally, near-instant signup). **Don't rely on tastytrade for this** — their international account review takes 3–5 business days minimum, which risks slipping past kickoff entirely.
- **Validate SL/TP calibration empirically (added 2.4).** Before trusting the revised 125% stop-loss (§8) or the 1–5 DTE band's implied breakeven win rate as final, run `win_rate_validator.py` (companion script, see §13) against this same EOD/IV pull — it backtests §5.1's Expected-Move-based strike placement across DTE and strike-distance combinations and reports the empirical win rate next to the breakeven threshold each SL setting requires. **If no combination clears breakeven at the current strike-placement logic, widen strike placement (increase distance from spot in EM% terms) before adjusting SL any further** — don't just keep loosening the stop to force a number to work.
- **Manually rehearse the Reflexion loop.** Feed a synthetic episode ("Iron Condor position hit stop-loss due to an IV spike") into FinMem and check whether the Chief Strategy Agent produces a concrete, trigger-specific rule (not a generic statement), and whether it's actually persisted to semantic memory.
- **Test the `index-data` and `options-data` toolsets separately (§12).** Expect `get_index_latest_values`/`get_index_values` to fail or return empty — that's the known gap. But `get_option_snapshot` on an ATM XSP contract should still return live IV/Greeks, since that rides the OPRA feed rather than the broken index-value feed.
- **Build the Tier 1 SPY-proxy fallback now, not conditionally (§12)** — treat it as the default, not a break-glass option, given the evidence that the index-value gap is still open.
- **Hardcode the three macro releases that fall inside the competition week (§12)**: JOLTS **and** ISM Manufacturing PMI, both Tue Sep 1, 10:00 AM ET (same minute — treat as one combined event window), and Nonfarm Payrolls Fri Sep 4, 8:30 AM ET.
- Pitch deck / video storyboard, and the one-page write-up (§10), drafted.
- **Team's chosen approach for the GitHub repo:** develop against a private/local repo pre-kickoff, then **create and push the public GitHub repository on Aug 28** alongside opening the fresh competition account.
- The only genuine hard line: **do not place any order on whichever account will be the final submission account** until it's actually the fresh, dedicated one opened at kickoff.

| Day | Focus |
|---|---|
| Fri Aug 28 (kickoff, 15:00 UTC = 11:00 AM ET) | Registration closes at kickoff — make sure the team is already registered. **Open the brand-new dedicated $100,000 Alpaca paper account and record its account ID** (§10); swap MCP credentials from the Day 0 scratch account to this one; **create and push the public GitHub repo**. Target the **first live multi-agent cycle** within the remaining ~5 hours of RTH (11:00 AM–4:15 PM ET). |
| Sat–Sun Aug 29–30 | **Exchange closed (weekend gap, ~51 hrs).** No live trading is possible on any Cboe session, RTH or GTH. Use this window for offline bug-fixing, reviewing Friday's execution log, and running the manual settlement-verification check (§9). |
| Mon–Thu Aug 31–Sep 3 | 4 full RTH days — the actual engine of the week. Lean toward the short end of the §8 DTE band (1–2 days) to maximize completed open→close cycles; FinMem/Reflexion refines semantic rules from real episodes; begin daily status.alpaca.markets check routine; kill-switch and TP/SL automation stress-tested against live results so far. **Tue Sep 1, 10:00 AM ET: JOLTS *and* ISM Manufacturing PMI release simultaneously** — Macro Analyst Agent treats 10:00–10:15 AM ET as one combined event window and holds new entries until both prints are out and XSP IV/skew has settled, same pattern already used for the Sep 4 NFP morning. Record B-roll/screen capture and finalize the pitch deck draft on Sep 3. |
| Fri Sep 4 (15:00 UTC = 11:00 AM ET deadline) | **NFP releases 8:30 AM ET (high importance), one hour before the open.** **Do not open new positions.** Once RTH opens at 9:30 AM ET, force-close all remaining positions from Thursday to lock in realized P&L before the 1.5-hour submission window closes. Record 5-min video, finalize PDF deck, confirm GitHub repo is public and MIT-licensed, confirm demo URL live on chosen host, confirm one-page write-up is attached, submit. **Do not leave the agent trading after submission — see §12.** |

---

## 12. Open Risks & Assumptions

**Still genuinely open:**
- The settlement bug (§9) could be fixed mid-week without notice, or could get worse — re-verify, don't assume the Aug 26 status holds all week. This is the one risk in this doc that cannot be closed out by planning; it depends on Alpaca shipping a fix.
- Judging-criteria *weighting* between the five dimensions (§10) isn't published numerically — treat P&L Performance as the lead one (it's visually featured) and the rest as roughly equal unless the kickoff stream says otherwise.
- **P&L judging-timing mechanics are not published by the event (added 2.4).** Neither the event page, the general Hackathon Rule Book, nor the "Delivering your hackathon solution" guide states whether judges evaluate the account's P&L exactly as of the Sep 4, 15:00 UTC deadline, or whenever they happen to review it afterward — review appears to be asynchronous, in the days following (the event page's own language is "Judges review every finalist," not a fixed-instant snapshot). Given this ambiguity, and independent of §9's settlement-bug rationale, the team's default is to force-close all positions and stop opening new ones before the deadline (already specified in §9/§11) rather than let the agent keep managing positions unattended post-submission: an inconsistent, unverifiable, or degraded post-deadline P&L is pure downside risk with no confirmed upside, and nothing in the published rules suggests trading activity after the deadline is credited toward judging. If certainty on this matters enough to the team's plan, ask directly via Discord `#ineedhelp` — this is exactly the kind of event-specific mechanical question that channel exists for.
- **Reviewed and explicitly rejected: `vilkovgr/0dte-strategies`, considered as a possible source for upweighting the §5.1 skew signal (added 2.4).** This is a 0DTE options-strategy working paper (author-stated as submitted to *Financial Analysts Journal*) with a public GitHub replication package. The paper's claimed Sharpe ratios initially matched a research summary handed to the team, and direct inspection confirmed the repository is real and substantive (runnable replication code, `tools/doctor.py`, data-ingest scripts against a commercial vendor feed and ThetaData). **However**, cloning the repo and running its own test suite found its `output/tables/` and `tests/reference/tables/` artifacts are **internally inconsistent** for the exact table the cited figures came from — different Sharpe ratios (e.g., Iron Condor conditional net SR: −0.20 in one committed artifact vs. −4.13 in the other; Put Ratio Spread conditional net SR: +0.93 vs. −0.70) and different observation counts (1,061 vs. 682) for what should be the same full-sample calculation. The repository's own `KNOWN-ISSUES.md` separately documents an August 2026 transaction-cost unit-scale bug ("code fixed, paper revision in progress") that further supersedes the originally-cited numbers. **Conclusion: this source failed the team's own verification protocol and is not cited or relied upon anywhere in this PRD.** No change was made to §5.1's skew-signal weighting as a result — it remains as specified since 2.1 (a minor tiebreaker, not a co-equal input to IV Rank/Percentile).

**Index Options (XSP) Data Availability on Alpaca's Market Data API (CRITICAL):**
- Alpaca's own blog announcing index options in paper trading (July 23, 2026) states directly that Alpaca does not currently provide index data through its Market Data offering, and that this will be supported in the coming months.
- Stronger evidence this is still true: a June 3, 2026 changelog entry briefly listed `GET /v1beta1/indices/latest/values` and `GET /v1beta1/indices/values` as new endpoints — but a later note added to that same changelog page says those endpoints were not publicly available, and were removed on July 24, 2026, one day after the blog admitted the same gap.
- The MCP server's `index-data` toolset (`get_index_latest_values`, `get_index_values`) is a direct wrapper around this same unavailable endpoint — expect it to fail or return empty on Day 0, not just "possibly."
- **Important scope note:** this most likely affects only the raw index *spot value* (used for HV30's historical series and EM%'s `/Spot` denominator). Per-contract options data — IV, Greeks, bid/ask via `get_option_snapshot`/`get_option_chain` in the `options-data` toolset — rides the OPRA feed independently and should keep working. Test both separately on Day 0 rather than treating this as an all-or-nothing failure.
- **Mitigation (tiered — build Tier 1 now, not conditionally):**
  - **Tier 1 (default):** use SPY as a proxy for spot price and historical closes via Alpaca's own `stock-data` toolset — zero new integration, and order execution stays 100% on XSP contracts.
  - **Tier 2 (optional, if time allows):** a free Tradier sandbox account exposes SPX-family option chains with Greeks/IV computed by ORATS. Use read-only for cross-checking; don't make execution depend on it.
  - **Tier 3 (pre-kickoff historical backfill only):** `yfinance` for `^GSPC` — free, unofficial, not for live reliance.
  - Ruled out: ORATS directly (no free tier, $99/mo minimum) and Polygon.io (free tier is 5 calls/min with EOD/15-min-delayed data and gates certain indices to paid tiers).

**Macro Economic Calendar — no live API needed:**
- Alpaca has no built-in macro calendar (its `get_calendar` tool is exchange hours/holidays only).
- Because the competition window is fixed and only 7 days, the pragmatic answer is to **hardcode the known releases** rather than integrate a live third-party API: **JOLTS, Tue Sep 1, 10:00 AM ET**, **ISM Manufacturing PMI, also Tue Sep 1, 10:00 AM ET** (the exact same minute as JOLTS — treat as one combined medium-high-importance window, not a single medium-importance JOLTS flag), and **Nonfarm Payrolls, Fri Sep 4, 8:30 AM ET**. No CPI and no FOMC meeting fall in this window.
- If a live feed is still wanted for the "Technology Implementation" narrative, Finnhub is the only candidate with a genuinely free, self-serve tier among those evaluated — but verify on Day 0 that forward-looking scheduled events are included on the free key.
- Ruled out: FXStreet's Economic Calendar API (sales-gated, no self-serve pricing); Trading Economics (discontinued free sample access); Tradefeeds (~$99/mo cheapest, no free tier).

**Cross-verification tooling (for §5.1's Quant Engine, not for execution):**
- thinkorswim Guest Pass (Schwab) — free, no funded account, available internationally, near-instant signup — use to manually sanity-check IV Rank/EM% output.
- tastytrade — do **not** plan around this for pre-kickoff verification; international account approval runs 3–5 business days minimum and risks missing the Day 0 window entirely.

---

## 13. Sources

- Alpaca Community Forum — "Paper trading bugs with index option settlement," forum.alpaca.markets/t/.../19441, Aug 3–6, 2026 (bug report + `grace_alpaca` staff confirmation)
- Alpaca API Changelog — docs.alpaca.markets/us/changelog (re-checked through late Aug 2026; no settlement fix present)
- Alpaca Status Page — status.alpaca.markets
- lablab.ai — Alpaca AI Trading Agents Hackathon event/live pages, general Hackathon Rule Book, and "AI Hackathons: The Complete Guide" — dates, prize pool, deliverables, and Streamlit/Replit/Vercel hosting requirement directly confirmed
- This event's own **Judging Criteria** and **Account Requirements** pages — source for the P&L Performance-led 5-criteria judging model (§2, §10) and for the "any paper account during development" build-window confirmation (§10–§11)
- HireToday hackathon listing — secondary corroboration of the "new dedicated Alpaca paper trading account" eligibility requirement
- lablab.ai — "AI Hackathons: The Complete Guide" — general "core AI functionality must be built during the event window" FAQ, superseded here by this event's own Account Requirements copy
- Alpaca Docs — docs.alpaca.markets/us/docs/alpaca-mcp-server: 65 tools across 11 toolsets
- BIS Working Papers No. 1294, "Parsing the pulse: decomposing macroeconomic sentiment with LLMs," Kwon, Park, Rungcharoenkitkul & Smets, Oct 2025
- arXiv 2605.19337, "Agentic Trading: When LLM Agents Meet Financial Markets," Xia et al., May 2026
- Alpaca Blog — "Index Options Now in Paper on Alpaca's Trading API," July 23, 2026 — confirms Market Data API does not yet support index data
- Alpaca Docs — Market Data API changelog entry, originally posted June 3, 2026, updated July 24, 2026 — confirms the indices/values endpoints were announced then withdrawn
- GitHub — alpacahq/alpaca-mcp-server README — toolset/tool inventory confirming `index-data` wraps the unavailable endpoints while `options-data` rides the OPRA feed
- Cboe / Morgan Stanley Global Trading Hours disclosure — GTH/Curb sessions for SPX/VIX/XSP
- Cboe — VIX Historical Data page — free daily VIX CSV, no signup required
- Charles Schwab International — thinkorswim Guest Pass — confirms international availability with no funded account required
- tastytrade Support — international customer account documentation — confirms 3–5 business day manual review
- FedRateCalc — "2026 U.S. Economic Calendar," sourced from BLS/BEA/Census/Federal Reserve official release schedules — source for JOLTS (Sep 1) and Nonfarm Payrolls (Sep 4) dates
- FXStreet Docs, Trading Economics pricing comparison, Tradefeeds pricing — confirm each is not viable on a 2-day runway to kickoff
- Finnhub Docs — Economic Calendar endpoint — flags historical events/surprises as Enterprise-only
- Black, K. (CAIA Association) & Szado, E. (Providence College / INGARM), "Performance Analysis of CBOE S&P 500 Options-Selling Indices," commissioned by CBOE, Feb 23, 2016 — source for §3's Iron Condor (CNDR) volatility/tail-risk comparison. **Scope note:** CNDR is a monthly-cadence benchmark, not a 1–5 DTE strategy — used as strategy-*type* evidence, not DTE- or strike-specific evidence.
- Cboe — CNDR and BFLY methodology/index-construction pages — corroborates the Black & Szado study
- arXiv 2508.16598, Wysocki, "Sizing the Risk: Kelly, VIX, and Hybrid Approaches in Put-Writing on Index Options," Aug 2025 — source for §8's VIX9D-scaled Kelly-fractional sizing. **Scope note:** studies naked/single-leg short put-writing; only the sizing-methodology finding is adopted, not its strike/DTE recommendations.
- Anthropic — Claude Platform pricing page — confirms Claude Sonnet 5's $2/$10 per-million-token rate is standard and permanent; Claude Haiku 4.5 at $1/$5. Source for §5.3's cost math.
- Anthropic — Models API reference — confirms `structured_outputs` as a first-class capability field. Source for §5.3's reliability rationale.
- Anthropic — Claude Code legal and compliance page — confirms OAuth tokens from Free/Pro/Max plans are restricted to Claude Code and claude.ai; developers integrating Claude into other products/services must use a standalone Anthropic API key. Source for §5.3's auth guidance and the rejected-OAuth-workaround note.
- Framework comparisons (LangChain/LangGraph resources; Uvik, Arsum, AYAutomate 2026 AI-agent-framework guides) — consensus that LangGraph is the production-grade choice for stateful, checkpointed, multi-agent graphs. Source for §5.3's orchestration decision.
- Third-party Claude Agent SDK reviews (aiagentshub.net, helply.com) — confirm the SDK is explicitly Claude-model-only and scoped around a single agent session. Source for §5.3's SDK-rejection rationale.
- The Register, "Anthropic clarifies ban on third-party tool access to Claude," Feb 20, 2026 — dates the OAuth-restriction policy, notes the underlying Consumer ToS basis predates it.
- OpenClaw Launch, GitHub `AndyMik90/Aperant` issue #1871, and OpenClaw.report — independent corroboration of the OAuth-token restriction's scope and enforcement. Source for §5.3's "explicitly rejected" note.

**Sources added in 2.4 (verified Aug 28, 2026):**
- projectfinance.com (Chris Butler), "Iron Condor Management Results from 71,417 Trades" — backtest of SPY iron condors (16-delta/5-delta and 30-delta/16-delta variants), Jan 2007–Mar 2017, 16 TP/SL management combinations filtered by VIX quartile at entry. Source for §8's stop-loss revision (200% → 125%). **Scope note, stated explicitly to prevent overclaiming (same pattern as the Wysocki and Black & Szado entries above):** the underlying trades are 45 DTE (30–60 day expiration cycles), not DTE-matched to AeroQuant's 1–5 day band, and the study's own findings are nuanced, not a blanket "tighter stops win" result — for 30-delta condors generally (not filtered by IV), looser 200%-credit stops actually **outperformed** tighter ones and no-stop-at-all. Only the **high-IV-at-entry-specific** finding (tighter stops outperform when IV is elevated at entry) is adopted here, reasoned from §5.1's IV Rank/Percentile > 60 gate placing every AeroQuant entry in that specific bucket. The exact 125% figure is a reasoned starting point, not a number this source proves optimal for AeroQuant's DTE band — see §11's empirical-validation action item.
- GitHub — `vilkovgr/0dte-strategies`, working paper "0DTE Trading Rules: Tail Risk, Implementation, and Tactical Timing" — reviewed as a possible source for §5.1 skew-signal weighting, found to have internally inconsistent replication artifacts (see §12) and explicitly **not** cited or relied upon anywhere in this PRD. Documented here for audit-trail completeness only — this is a rejected source, not a supporting one.
- lablab.ai event page, general Hackathon Rule Book, and "Delivering your hackathon solution" guide, re-checked Aug 28, 2026 — no explicit language found on P&L-judging-snapshot timing; see §12.
- `win_rate_validator.py` (internal companion script, not an external source) — computes empirical win rate from §5.1's Expected-Move-based strike-selection logic against real historical underlying + IV data, for comparison against the breakeven win rate implied by whatever SL/TP setting §8 specifies. Referenced in §11's Day 0 checklist.
