# PRD — AeroQuant 2.6: VRP Harvester
**Autonomous AI Options Trading Agent — Alpaca AI Trading Agents Hackathon**
**Consolidated final — supersedes PRD 2.5.**

| | |
|---|---|
| **Event** | Alpaca AI Trading Agents Hackathon (lablab.ai) |
| **Track** | Options Alpha Agents |
| **Dates** | Aug 28 – Sep 4, 2026 (kickoff 15:00 UTC / 22:00 WIB, submission deadline Sep 4 15:00 UTC / 11:00 AM ET — see §11 for a WIT-labeled screenshot showing 13:00 UTC; reconfirm live before Friday, most likely a WIB/WIT mislabel on the site, not an actual moved deadline) |
| **Prize pool** | $6,300 total (confirmed via live results page screenshot, Aug 29, 2026): $5,000 main prizes (1st $2,500 + $300 in Featherless credits, 2nd $1,500, 3rd $1,000) + $1,000 Social Engagement (2 winning teams × $500) + $300 Featherless credit bonus |
| **Version note** | **2.6 changes from 2.5:** (1) §11b (new) formalizes that **the team runs three parallel official strategy accounts, one per member, Mon Aug 31 – Thu Sep 3**, comparing equity Friday morning and submitting whichever performed best — see §11b for why the Thursday-flat deadline (§9) applies to **all three accounts**, not just whichever ends up submitted, since the winning account isn't known until after the deadline has already passed. (2) §5.3's Featherless model selection is **not yet finalized to GLM** — GLM-4.7-Flash/GLM-5.2 are real, catalogued Featherless models and a cheaper/higher-concurrency option than Qwen3-32B, but Featherless's *documented* native-tool-calling guarantee (the reason Qwen3 was chosen originally) has not been confirmed to extend to GLM. **Qwen3-32B remains the active default until the corrected §4b-style smoke test (RUNBOOK) passes with GLM specifically forced to answer.** (3) VPS reality check added to the RUNBOOK: actual provisioned spec (1 vCPU / 2GB RAM) is smaller than earlier planning assumed (2 vCPU / 4GB) — swap, per-unit memory limits, and per-person resource isolation added accordingly; see RUNBOOK §2/§8. **No change to §3's strategy matrix, §5.1's signal weighting, §7's core execution rules, or §8's risk framework.** |

---

## 1. Executive Summary

AeroQuant 2.0 is an autonomous, multi-agent options trading system built for Alpaca's paper-trading environment. It harvests the **Volatility Risk Premium (VRP)** — the well-documented tendency for implied volatility to run ahead of realized volatility — using defined-risk, non-directional options structures on European-style, cash-settled index options (XSP). The system pairs a **deterministic quantitative engine** (no LLM math) with a **hierarchical multi-agent LLM reasoning layer**, a **layered memory architecture (FinMem)**, and **verbal reinforcement learning (Reflexion)** so the agent can adapt its own rules mid-competition without retraining any model weights.

Revision history in brief: 2.1 added the settlement-bug guardrail and hackathon submission mechanics. 2.2 locked in direct Anthropic API + LangGraph. 2.3 closed out a rejected OAuth cost-optimization idea. 2.4 recalibrated §8's stop-loss from a verified backtest and added the empirical win-rate validation step. **This revision (2.5)** is a consolidation: it folds in a confirmed secondary LLM provider (Featherless), reconciles the operational timeline against a now-verified official FAQ/Discord source, and retires the separate Architecture Discussion Pack by absorbing its finalized decisions directly here — while explicitly preserving, not erasing, the small number of items that document still listed as genuinely undecided.

---

## 2. Goals & Success Metrics

The product's only real customer during evaluation week is the judging panel. This event's own Judging Criteria page confirms a P&L-led model, not lablab.ai's generic platform-wide 4-dimension template.

| Judging dimension | What it rewards | AeroQuant 2.0 commitment |
|---|---|---|
| **P&L Performance** *(visually the lead criterion)* | Actual P&L and how effectively the strategy performs through its trading activity | §9's settlement-bug guardrail and §11's "agent already mature before kickoff" plan both exist so a strategy that's still being debugged on Day 3 doesn't lose independent trade cycles |
| **Technology Implementation** | Effective use of Alpaca's Trading API, MCP server, CLI, and other required tech | Hybrid Intelligence split (deterministic engine vs. LLM reasoning); full use of Alpaca's Trading API, MCP Server (§7), and options data; confirmed use of a hackathon tech partner (Featherless, §5.3) |
| **Creativity & Originality** | Originality of concept, strategy, agent behavior, approach | Adversarial Bull/Bear debate + Reflexion-driven semantic memory, rather than a single-prompt trading bot |
| **Presentation & Execution** | Clarity of communication, demonstrating the agent in action, explaining reasoning | 5-minute video and slide deck drafted well before Day 7; demo shows the agent's actual reasoning trail, not just a final number |
| **Social Engagement** *(bonus, not core)* | Content quality and engagement it generates | Low-effort bonus layered on top of the core four — never competes for time against P&L Performance |

Internal engineering success metric: **verifiable, risk-adjusted P&L** over the 5–7 trading days, with zero unhandled exceptions, zero naked/undefined-risk positions, and zero reliance on unverified paper-account settlement math (§9).

---

## 3. Strategic Foundation: Harvesting the Volatility Risk Premium

*(Unchanged from 2.4.)*

The Volatility Risk Premium is the persistent gap between an option's Implied Volatility (IV) and the Realized Volatility (RV) the underlying actually delivers. IV exceeds RV in roughly **83–87% of rolling 30-day windows** on the S&P 500 going back to 1990. Given a 7-day evaluation window, the agent does **not** attempt to forecast direction — the mandate is a non-directional or mildly directional short-premium book, harvesting Theta and Vega rather than guessing Delta.

**Core strategies:**
- **Iron Condor** — sell an OTM put spread and an OTM call spread simultaneously; the dominant, default play used whenever the regime is neutral-to-ambiguous.
- **Bull Put Spread / Bear Call Spread** — directional credit spreads, used only as a narrow, regime-conditional override when the Technical Manager (§5.2) detects a genuine mild trend alongside high IV.
- **Debit spreads and any other long-premium, direction-betting structure are explicitly excluded.**
- **Naked option selling is explicitly disallowed** — every position must be a defined-risk spread.

Why Iron Condor is the structural default (Black & Szado, 2016, CBOE-commissioned, 29.5 years of SPX benchmark data): CNDR posted the lowest annualized volatility (7.23%) of six options-selling benchmarks and far fewer large-loss months than the S&P 500 or single-sided PutWrite. This is strategy-type evidence, not DTE/strike-specific evidence, but it supports Iron Condor as the risk-adjusted default for a short, judged window where one bad tail event is more damaging than a slightly lower average return.

The four-strategy matrix is a pre-set policy, fixed before Aug 28, not a live experiment — with only ~4.5 effective trading days, splitting trade count across strategy types would leave each with too few completed cycles to mean anything, and risks an incoherent story for judges.

---

## 4. Instrument Selection: Why XSP, Not SPY or SPX

*(Unchanged from 2.4.)*

Standard equity/ETF options (SPY, QQQ) are American-style: early exercise exposes a short-premium book to assignment, dividend, and overnight pin risk. European-style, cash-settled index options (SPX/XSP) eliminate that failure mode: cash settlement (no accidental equity accumulation) and no early exercise (mathematically restricted to expiration).

| Instrument | Notional per contract | Fit for a $100k paper account |
|---|---|---|
| SPX | ~$600,000 | Too large for precise position sizing |
| **XSP** | **~$60,000** (1/10 multiplier) | **Sweet spot** — SPY-like granularity with SPX-like structural safety |

**Decision: anchor the entire strategy to XSP.**

---

## 5. System Architecture: Hybrid Intelligence

**Core design principle:** *the LLM must never be trusted to calculate a number, and the deterministic engine must never be trusted to interpret qualitative nuance.*

### 5.1 Deterministic Quantitative Engine (Python / NumPy / Pandas, non-LLM)

*(Unchanged from 2.4.)* Computes before any LLM call: HV30 (`std(ln(Pt/Pt-1)) × √252`), IV Rank & IV Percentile (both >60 is the strongest mandate to authorize short-premium strategies; treat as low-confidence until 10+ trading sessions of local IV history exist, corroborate via VIX/VIX9D term structure until then), Expected Move (`EM% = (Mid ATM Call + Mid ATM Put) / Spot × 100`), Volatility Skew (25-delta; minor tiebreaker only — see §12 for a source investigated and rejected for upweighting this), and Momentum Z-Score (20-day).

### 5.2 Hierarchical Multi-Agent System

*(Unchanged from 2.4.)*

| Agent | Substrate | Role |
|---|---|---|
| Data Gatherer | Python / API script | Pulls Alpaca market data, options chains, news |
| Quant Engine | Python / NumPy | Computes §5.1 metrics — no LLM involved |
| Volatility Analyst | Claude Haiku 4.5 | Classifies the volatility regime |
| Macro/News Analyst | Claude Haiku 4.5 | Reads Alpaca News API; qualitative circuit breaker ahead of scheduled macro events |
| Technical Manager | Claude Sonnet 5 | Synthesizes Volatility + Macro into directional conviction |
| Bull Researcher / Bear Researcher | Claude Sonnet 5, adversarial pair | Bull argues the premium-harvest case; Bear attacks it, flags skew anomalies and event risk |
| Chief Strategy Agent | Claude Sonnet 5, forced tool-use / structured output | Arbitrates the debate, selects one strategy, specifies exact strikes/expirations from a pre-validated liquid-contracts list |
| **Risk Manager** | **Python, deterministic, non-bypassable** | Final gate — rejects any proposal violating hard risk limits, logs, halts the cycle |

### 5.3 LLM Provider & Orchestration Decision — updated in 2.5

**Primary decision (unchanged since 2.2, re-confirmed 2.3/2.4): direct Anthropic API (Claude Sonnet 5 + Claude Haiku 4.5), orchestrated by LangGraph.** Anthropic's pricing ($2/$10 per M tokens Sonnet 5, $1/$5 Haiku 4.5) keeps full-week spend under $10 against ~750 total calls; `structured_outputs` is a first-class, documented capability addressing the "invalid JSON from Chief" risk without a beta translation layer; auth is a standalone Console API key, per Anthropic's own developer-integration terms.

**New in 2.5 — confirmed secondary provider: Featherless.ai.**
- **Status: confirmed, not speculative.** Featherless is a listed hackathon tech/prize partner (1st-place prize includes a $300 Featherless credit bonus, confirmed via the live results page), and the team holds a paid Featherless Developer subscription (5× accounts, evidence: team screenshot of the Featherless pricing page, Aug 29 2026 — Developer tier, $50 credits/month base, 256K context, 1 agent environment included, unused credits roll over).
- **Role:** secondary/fallback provider for `fast_analysis` and, pending team sign-off, `strong_reasoning`/`critic` roles when Anthropic is degraded — using `model_gateway.py`'s existing circuit-breaker + fallback-chain design, **with the BytePlus Ark candidate removed** (see below) and Featherless promoted to the sole non-Anthropic candidate per chain.
- **Model:** Qwen3-32B **remains the active default**, chosen in the existing implementation specifically because Featherless documents native tool-calling support for it (other Featherless model families can return tool calls as plain text, breaking structured-output parsing silently) — this reasoning still holds and is retained. **Candidate swap under evaluation (2.6, not yet adopted): GLM-4.7-Flash (`fast_analysis`) / GLM-5.2 (`strong_reasoning`, `critic`)** — both are real, catalogued Featherless models, cheaper and higher-concurrency than Qwen3-32B, and GLM-4.7 does have a dedicated tool-call parser in its reference serving stack (SGLang) — but Featherless's own documentation of *guaranteed* native tool-calling reliability, the specific reason Qwen3 was chosen, has not been confirmed to extend to GLM. **Do not switch the live default until the RUNBOOK's Featherless-forced smoke test passes with GLM specifically answering** — see RUNBOOK §4b for the corrected test procedure (the original draft of this test incorrectly suggested unsetting `ANTHROPIC_API_KEY`, which crashes `ModelGateway`'s constructor with a `KeyError` before any fallback logic runs; the fix is to set it to a present-but-invalid value so construction succeeds and the failure happens at the API-call layer, where the existing try/except correctly catches it).
- **Open item before this goes live in the scored pipeline (not yet resolved by this document):** the fallback chain has never been smoke-tested end-to-end against the real Featherless endpoint (only the bare Anthropic client has been smoke-tested, per the runbook). `requirements.txt` is also currently missing `langchain-openai`, which `model_gateway.py` requires to import at all. Both must be fixed and the three-role smoke test in `model_gateway.py`'s own `__main__` block must actually be run against real credentials before this is trusted in a scored cycle.
- **Explicitly deferred, not adopted: BytePlus Ark.** Present in the current `model_gateway.py` file as a third candidate, but no partner relationship or paid-account evidence has been shown for it (unlike Featherless). Recommendation: drop it from the live chain unless/until equivalent evidence is provided — carrying an unverified, unsponsored third provider into the critic/strong_reasoning path for a scored trading agent is a decision the team should make deliberately, not by default because the code already has it wired.
- **Strategic caveat, carried forward regardless of provider mix:** `critic`/`strong_reasoning` are the roles that gate real order placement. A non-Anthropic fallback taking over those roles during exactly the moments Anthropic is degraded (plausibly correlated with high system-wide load / high market volatility) is a real quality trade-off, not a free reliability win. Decide the fallback policy per role, not just per provider list — e.g. it may be reasonable to let Featherless serve `fast_analysis` freely but require a human/Telegram-alert pause rather than auto-fallback for `critic`.

**Orchestration — LangGraph, not a chat-CLI agent.** *(Unchanged from 2.4.)* A vendor-native Claude Agent SDK option was considered and rejected for the core trading loop (Claude-only by design, built around a single agent, not a multi-role checkpointed graph). Routing calls through a Claude Code subscription OAuth token was raised and explicitly rejected in 2.3 (ToS violation, technically blocked at the API layer since Jan 2026, no cost upside given the <$10/week spend) — this remains closed and is unaffected by the Featherless addition, which uses its own separate, legitimately paid API key.

### 5.4 Engineering Architecture — consolidated from the Architecture Discussion Pack (v0.2, now retired as a standalone file)

The standalone `AeroQuant_Agent_Architecture_Discussion_v0_2.md` document is retired; its **finalized** decisions are folded in below. Items that document itself still listed as open (§20/§22 of that file) are carried forward as open here too — they are genuinely unresolved team decisions, not something a document merge can close on its own.

**Confirmed architecture (from the Discussion Pack, now final):**
- **Four planes:** Evidence, Reasoning, Safety and Execution, and Learning. The strategy/quant layer can change later without rewriting the other planes.
- **Integration approach:** modular vertical slice — preserve existing Alpaca/MCP/LLM/evaluation/dashboard components behind explicit interfaces; introduce typed, versioned contracts between every stage; replace the monolithic cycle with a checkpointed workflow coordinator; keep all Alpaca trading actions behind deterministic services (LLM agents never receive trading tools directly); make broker-confirmed fills/closes authoritative before positions or outcomes are recorded; add the Bull/Bear debate and governed Reflexion loop only after the execution lifecycle is trustworthy.
- **LangGraph node/edge structure:** `precheck → evidence → quant → candidates → memory → [volatility ‖ macro] → technical → [bull ‖ bear] → chief → validator → risk_gate → persist`.
- **Checkpointer `thread_id` convention:** `f"{alpaca_account_id}:{cycle_id}"` — not just `cycle_id` — so state stays account-scoped across the scratch → official account cutover.
- **Chief ↔ proposal_validator repair loop** must carry a hop counter in `CycleState` so a bad proposal cannot loop forever inside one cycle.
- **§9's force-close-before-expiry guardrail** is a mandatory, always-on scheduled job in the execution plane — not left to the black-box Exit Policy.
- **Contracts in scope, implementation not yet frozen:** `CandidateBuilder`, `TradingMandate`, `ExitPolicy` — defined as replaceable black-box boundaries; the strategy logic behind them stays owned by §3/§8 of this PRD, not this section.
- **Operational store:** SQLite WAL for the competition, behind a repository boundary compatible with a future Postgres swap.
- **Deployment split:** the VPS hosts the private worker and a read-only API only; the public demo is a separate Streamlit/Replit/Vercel deployment consuming that API (mandatory per §10's deliverables).
- **Debate depth:** one independent Bull round and one Bear round, then Chief arbitration (no extra rebuttal round, for latency).

**Still genuinely open (not resolved by this consolidation — needs a team decision, tracked here so it isn't lost when the standalone file is retired):**
- Embedding provider for semantic memory (Anthropic's Messages API doesn't serve embeddings — needs a separate provider decision).
- VPS geographic placement — Indonesia-hosted is architecturally acceptable per the Discussion Pack's own analysis, pending the Day-0 reachability test (§1 of the RUNBOOK) actually passing.
- Alert channel selection for account mismatch / orphan state / failed emergency close (RUNBOOK §9 already implements Telegram — recommend treating this as closed by implementation unless the team objects).
- Rule-governance threshold for auto-activating Reflexion-learned soft rules during the competition.
- Integration/release DRI (single owner for contract freeze and go/no-go) — unassigned.
- MCP compliance interpretation: whether a deterministic EvidenceGateway satisfies the "must use MCP or CLI" hard gate, or whether a dedicated read-only Data Gatherer tool-call path is required as a fallback — recommend resolving this via Discord `#ineedhelp` given it's a hard eligibility gate, not just a style preference.

---

## 6. Memory & Adaptation: FinMem + Reflexion

*(Unchanged from 2.4.)* Verbal reinforcement learning — natural-language rule adaptation, not weight updates. Layered memory: working (clears per cycle), episodic (immutable log, queryable by similarity), semantic (generalized rules injected into system prompt). Reflexion loop on every position close: outcome ingestion → root-cause critique (flawed logic vs. macro shock vs. variance) → actionable, trigger-specific rule refinement (generic statements rejected) → memory injection as a binding future constraint.

---

## 7. Execution Infrastructure: Alpaca MCP Server

*(Unchanged from 2.4.)* Official Alpaca MCP Server, 65 tools across 11 toolsets. `ALPACA_TOOLSETS` scoped to `account, trading, assets, options-data, stock-data, index-data`. `get_option_contracts` lives in `assets`, not `options-data` — required for the Chief to enumerate tradable contracts. Multi-leg orders only (`MLEG`, atomic fill — no legging in). Limit orders only, no market orders for options; pad ±5% toward the adverse side off the aggregate mid price.

---

## 8. Risk Management Framework

*(Unchanged from 2.4.)* The Risk Manager (§5.2) is the ultimate arbiter, not the agent's intelligence.

| Parameter | Rule | Rationale |
|---|---|---|
| Risk-adjusted sizing | Max loss per trade ≤ a fractional-Kelly base %, scaled down as VIX9D percentile rises | Absolute vol level is a second, independent risk control layered on top of the IV-Rank entry filter |
| Spread width | Max loss computed exactly from width − net credit | Risk is calculated, never assumed |
| Liquidity guardrail | Bid-ask spread capped as a % of mid-price | Avoids slippage-inflated mark-to-market losses |
| Max exposure | Capped % of buying power; max 6 concurrent positions | Prevents over-leverage and correlated blowups |

**Compressed-window tactics:** TP automation at 50% of max credit; SL at 125% of initial credit (revised in 2.4 from 200%, reasoned specifically through the IV Rank>60 entry gate against a 71,417-trade backtest — see 2.4's version note for the full scope caveat). DTE band 1–5 days (0DTE avoided — extreme intraday gamma risk). Kill switches: halt new entries at -3% daily realized P&L, or 5 consecutive Risk Manager rejections. **Validate empirically before trusting this in the scored week — `win_rate_validator.py`, §11 Day 0 checklist.**

---

## 9. ⚠️ Known Platform Risk: Confirmed Paper-Settlement Bug — MANDATORY GUARDRAIL

*(Unchanged from 2.4.)* **Status as of Aug 26, 2026: confirmed by Alpaca (`grace_alpaca`, Aug 6), unresolved, no fix ETA.** Out-of-the-money short legs are credited cash instead of floored to zero at settlement, and the settlement index value doesn't always match the official close — a documented four-session test showed a **~$9,700 error on a $100k account**. **Mandatory: force-close every short leg before expiry, never let a position settle naturally through paper expiration.** Daily manual verification routine (open one small OTM short, let it expire, diff reported cash flow against correct intrinsic value). Log this as an explicit assumption in the pitch deck — a known-and-guarded-against platform bug is a legitimate technical-depth story.

---

## 10. Hackathon Operational Compliance — updated in 2.5

Verified against the event's own live page (About, Challenge, Account Requirements, Judging Criteria, Event Schedule, What to Submit), lablab.ai's general rule book, **and, new in 2.5, the official Alpaca FAQ and a Discord Q&A exchange the team has independently verified as authentic** (produced after PRD 2.4, from the same Discord discussion this FAQ summarizes).

**Core challenge requirements (hard gates):** autonomous agent using Alpaca's Trading API; must use Alpaca's MCP server or CLI; all strategies must incorporate options trading (XSP-only design satisfies this by construction — a mid-week drift toward pure equity/ETF trades would fail this gate).

**Account requirements:** any paper account may be used freely during development (first-party confirmed — the Day 0 plan in §11 relies on this). The final submission must run on a **brand-new, dedicated Alpaca paper account created for this hackathon**, starting balance exactly $100,000 — *"Projects run on an existing or reused account will not be eligible for judging."* This is a hard eligibility gate, not a scoring deduction.

**Mandatory deliverables:**
- [ ] Basic info: project title, short/long description, technology & category tags
- [ ] Cover image
- [ ] Pitch video, ≤5 min, MP4
- [ ] Slide deck, PDF
- [ ] **GitHub repository — may remain private through the build week (per the FAQ, new in 2.5); must be public and MIT-License-compliant by submission** (this was already a confirmed deliverable in 2.4; the FAQ only relaxes *when* it needs to go public, not whether)
- [ ] Demo application hosted on Streamlit, Replit, or Vercel
- [ ] Application URL live and reachable
- [ ] Original work, open source, MIT-License-compliant — audit finance/quant dependencies for GPL/proprietary conflicts before adding
- [ ] New, dedicated Alpaca paper account, $100,000 starting balance, account ID included in submission — never trade anything else on it
- [ ] One-page write-up: AI logic, risk gates, Alpaca infrastructure implementation
- [ ] **New in 2.5 — mandatory: disclosure of any pre-kickoff work**, stated plainly in the README or write-up (what was built before Aug 28, per §11's Day 0 plan, vs. during the official window). Confirmed mandatory per the FAQ.
- [ ] (Optional) up to 5 social media post links

**"Build in Public":** share progress on X and LinkedIn, tag lablab.ai and Alpaca in every post, up to 5 links submitted. Bonus layered on the four core criteria — minimal time budget.

**Judging criteria:** P&L Performance (lead), Technology Implementation, Creativity & Originality, Presentation & Execution, Social Engagement (bonus). **New in 2.5, reconciled:** the FAQ frames this more simply as *"a combination of trading performance and P&L... and the creativity, autonomy, and robustness of the agent trading workflow"* with an explicit statement that P&L is not the sole factor. Read as compatible with the 5-criteria breakdown above, not competing — same substance, different granularity. No numeric weighting published either way; still treat P&L Performance as the lead criterion (visually featured) and the rest as roughly equal.

**New in 2.5 — platform/provider/hosting confirmation:** the FAQ states no restrictions on strategy, model provider, or hosting infrastructure (confirmed three times in the source). This directly supports §5.3's Featherless addition — nothing in the rules blocks a second provider, the only open question is whether the *team* wants it in the critic/strong_reasoning path, not whether the *platform* allows it. Options order types via MCP confirmed: market, limit, stop, stop-limit (trailing-stop is stocks-only) — validates §7's existing limit-orders-only discipline as a deliberate choice, not a platform limitation. Free-tier market data confirmed real-time for the latest quote — only historical/backtest-style pulls are delayed on Basic; live IV/EM% computation in §5.1 is unaffected.

**Build-window compliance:** *"use any paper account you like during development"* — first-party confirmation that maturing the full agent, including the LLM reasoning loop, before Aug 28 is fine. Only hard constraint is the account-freshness gate above.

**To watch:** re-check the event page's Technology Partners list right at/before kickoff — Featherless is now confirmed (§5.3); check whether any other announced partner fits naturally before forcing an integration that doesn't belong.

---

## 11. 7-Day Execution Timeline — updated in 2.5

**Corrected trading calendar:**
- **Fri Aug 28 (kickoff, 15:00 UTC = 11:00 AM ET / 22:00 WIB):** ~5 hours of RTH remain that day; realistic first live-trading window is closer to the ~4 hours after the opening program's Discord Q&A (12:00–4:15 PM ET), not the full 5.
- **Sat–Sun Aug 29–30: exchange closed, full stop.** ~51-hour dead zone (last Curb close Friday through Sunday 8:15 PM ET GTH open) — zero trading possible on any calendar. Use for offline bug-fixing, reviewing Friday's log, and the manual settlement-verification check (§9).
- **Mon–Thu Aug 31–Sep 3: 4 full RTH days.** The actual engine of the week. Lean toward the short end of the §8 DTE band (1–2 days) to maximize completed open→close cycles. Tue Sep 1, 10:00 AM ET: JOLTS and ISM Manufacturing PMI release simultaneously — hold new entries 10:00–10:15 AM ET until both prints are out and IV/skew has settled.
- **New in 2.5 — Thursday Sep 3 close-out (replaces 2.4's "hold through Thursday, force-close Friday morning" plan):** the official FAQ contains two statements about P&L measurement timing that do not agree with each other (Thursday EOD equity vs. a Friday 9:30 AM ET raw snapshot) — see §12 for the full detail. **Adopted posture: stop opening new positions Thursday early-to-mid afternoon, and confirm zero open positions by 4:15 PM ET Thursday at the latest** (target closing well before that, not at the buzzer). This is the one posture that is safe under every reading currently in circulation (Thursday EOD, Friday 9:30 AM raw snapshot, or empirically-observed overnight settlement posting for cash-settled index options) — holding any position into Friday morning is safe under none of them. This supersedes 2.4's plan of holding through Thursday and force-closing at Friday's open.
- **Fri Sep 4: no scored trading.** Per the FAQ, the P&L measurement window itself closes 9:30 AM ET Friday — trading after that does not count toward score regardless of the Thursday-vs-Friday ambiguity. The platform submission deadline is 15:00 UTC / 11:00 AM ET / 22:00 WIB (**reconfirm live — a team screenshot shows "10:00 PM WIT," which converts to 13:00 UTC and doesn't match this figure or PRD 2.4's own sourcing; most likely a WIB/WIT mislabel on the site, not an actual moved deadline**), giving a genuine ~1.5-hour buffer after trading has already locked, for finishing video/deck/write-up/repo-publish — not a second trading window. The worker may keep running harmlessly for demo purposes Friday if desired; nothing it does Friday affects the score.

### 11b. Three parallel strategy accounts (new in 2.6)

The team is running **three official competition accounts in parallel, one per member**, each a genuinely fresh $100,000 account trading from Monday 9:30 AM ET under its own strategy configuration. Friday morning, the team compares all three accounts' total equity and submits whichever performed best — the other two are simply not submitted, not evidence of anything improper (each is a real account that traded the real scored week under its own team member's approach; nothing about this fabricates or backdates results).

**This changes who §9's Thursday-flat guardrail applies to.** Because the winning account isn't known until after Thursday's close has already passed, **all three accounts must independently be flat — zero open positions, nothing left to expire — by Thursday 4:15 PM ET**, not just whichever one is eventually chosen. An account that's "probably not the winner" is not exempt: if it turns out Friday morning to actually be the best performer but has an open position that runs into the settlement bug (§9) or misses the scoring snapshot (§9/§11), the team loses the option to submit its real result.

**Infrastructure implication:** each account needs its own systemd unit, its own `thread_id` prefix (`f"{alpaca_account_id}:{cycle_id}"`, already required by §5.4's checkpointer convention), and its own `reports/` output, so Friday's comparison is a simple equity read across three independent, non-interfering processes rather than something reconstructed after the fact. See RUNBOOK §2 for the concrete per-person VPS layout.
- **Event risk:** Nonfarm Payrolls drops 8:30 AM ET Sep 4, one hour before the exchange opens — irrelevant to strategy now since no new positions open Friday regardless, but worth noting for the demo narrative.

**Day 0 (Thu Aug 27 and earlier — pre-kickoff dev, confirmed fine per §10):** *(Unchanged from 2.4.)* Start the IV snapshot cron immediately — highest-leverage pre-kickoff task. Repo skeleton, CI, deploy target wired end-to-end. Quant Engine and Risk Manager written and unit-tested. MCP server + `ALPACA_TOOLSETS` validated end-to-end against a scratch account, including the full multi-agent pipeline and Reflexion loop. Forward-test: Quant Engine pulls data without erroring, an `MLEG` Iron Condor fills without reject, Risk Manager actually blocks a breach. Cross-check HV30/IV Rank/EM% manually via thinkorswim Guest Pass (not tastytrade — 3–5 day review risks missing kickoff). **Run `win_rate_validator.py` against real EOD/IV data before trusting the §8 SL/TP calibration** — if no DTE/strike-distance combination clears the breakeven win rate, widen strike placement before loosening SL further. Rehearse the Reflexion loop with a synthetic episode. Test `index-data` and `options-data` toolsets separately (expect `index-data` to fail/return empty — known gap; `options-data` should work, rides OPRA independently). **New in 2.5: also run `model_gateway.py`'s own three-role smoke test against real Anthropic + Featherless credentials before Aug 28**, and fix the missing `langchain-openai` dependency first (see §5.3).

---

## 12. Open Risks & Assumptions — updated in 2.5

**Resolved in 2.5 (was open in 2.4):**
- **P&L judging-timing mechanics.** 2.4 correctly stated no FAQ existed on this at the time. A FAQ has since been produced (post-2.4, from the Discord discussion referenced below) and the team has independently verified it as authentic. **The FAQ itself is internally inconsistent** — its Timeline section states *"total equity as of EOD Thursday Sep 3rd,"* while a separate FAQ-table entry three questions later states the measurement window *"ends at 9:30 a.m. ET on Friday, September 4, when a snapshot of total account equity will be taken."* These are not the same instant, and the FAQ does not reconcile them. A corroborating Discord exchange (kickoff week 2026) shows the same Alpaca staff answer the identical question twice, ~3 hours apart, with different answers; one team separately reported an empirical result that a cash-settled index-option expiry settlement posted overnight rather than same-day. **Adopted mitigation (§11): flat well before Thursday 4:15 PM ET close** — safe under every reading in circulation, and does not depend on this ever being formally reconciled by the organizers.
- **GitHub repo visibility.** Resolved per the FAQ — private is fine during the build, public is required at submission (already a confirmed §10 deliverable; only the *timing* was in question).
- **Pre-event work disclosure.** Now a confirmed mandatory deliverable item (§10), per the same FAQ.

**Still genuinely open:**
- The settlement bug (§9) could be fixed or worsen mid-week without notice — re-verify daily, don't assume Aug 26 status holds all week.
- Judging-criteria numeric weighting between the five dimensions still isn't published — P&L Performance treated as lead, rest roughly equal.
- **The FAQ's own internal Thursday/Friday inconsistency (above) is not something this document can close** — it is a genuine ambiguity in the organizer's own published material. The adopted Thursday-close posture is a risk-management choice, not a resolution of the ambiguity itself.
- **BytePlus Ark inclusion in `model_gateway.py` (§5.3)** — no partner or subscription evidence found; not adopted in this revision; needs an explicit team decision (keep with justification, or remove from the live fallback chain).
- **Submission-deadline WIT/UTC discrepancy (§11)** — reconfirm the live countdown directly close to Friday; current best read is 15:00 UTC (two independent sources agree), the WIT-labeled screenshot showing 13:00 UTC is most likely a site mislabel, not a moved deadline, but this should not be assumed without a final check.
- **Reviewed and explicitly rejected (2.4, unchanged):** `vilkovgr/0dte-strategies` as a source for upweighting the §5.1 skew signal — the repo's own committed test artifacts were found internally inconsistent for the exact cited table (e.g. differing Sharpe ratios and observation counts for what should be one calculation), and it separately documents its own August 2026 transaction-cost unit-scale bug. Not cited or relied upon anywhere in this PRD.
- **Architecture Discussion Pack open items, carried forward from §5.4:** embedding provider, VPS geographic placement confirmation, Reflexion rule-activation threshold, integration/release DRI, MCP compliance interpretation for the EvidenceGateway pattern.

**Index Options (XSP) Data Availability — unchanged from 2.4:** Alpaca's Market Data API does not currently provide index spot values (confirmed via Alpaca's own blog and a withdrawn changelog endpoint); expect the MCP `index-data` toolset to fail/return empty. Per-contract options data (`options-data` toolset, OPRA feed) is unaffected. Mitigation: Tier 1 (default, build now) — SPY as spot proxy via `stock-data`. Tier 2 (optional) — Tradier sandbox for cross-checking. Tier 3 (pre-kickoff backfill only) — `yfinance` `^GSPC`.

**Macro Economic Calendar:** no live API — hardcoded releases: JOLTS + ISM Manufacturing PMI, both Tue Sep 1, 10:00 AM ET (treat as one combined window); Nonfarm Payrolls, Fri Sep 4, 8:30 AM ET (no longer strategy-relevant given the Thursday-close posture, but worth noting for the demo).

---

## 13. Sources

*(2.4's full source list is unchanged and retained — see prior revision for the full academic/documentation citation set: Black & Szado 2016, Wysocki 2025, projectfinance.com backtest, Alpaca forum/changelog/status/docs, Anthropic pricing/models/legal docs, LangGraph/LangChain framework comparisons, etc.)*

**Sources added in 2.5:**
- Team screenshot, Featherless.ai pricing page, "Developer" tier — $50 credits/month base, 256K context, 1 agent environment, unused credits roll over, billed per token. Evidence for §5.3's confirmed-secondary-provider decision, alongside the team's existing paid subscription (5 accounts).
- Team screenshot, Alpaca AI Trading Agents Hackathon live results/prizes page — total prize pool $6,300, 1st place includes "$300 in Featherless credits," submission deadline displayed as "Sep 4, 10:00 PM WIT." Evidence for §5.3's partner confirmation and §11/§12's deadline-reconciliation flag.
- Official Alpaca AI Trading Agents Hackathon FAQ (produced after PRD 2.4, from the Discord discussion below) — independently verified as authentic by the team. Primary source for §10/§11/§12's GitHub-repo-timing, pre-event-disclosure, market-data-tier, provider/hosting, and P&L-measurement-window findings. **Noted explicitly: contains an internal inconsistency between its "EOD Thursday Sep 3rd" and "9:30 AM ET Friday snapshot" statements, unreconciled in the source itself — see §12.**
- Discord Q&A exchange, kickoff week 2026, corroborating the FAQ's internal inconsistency (same staff member answering the same question twice, ~3 hours apart, with different answers) and an empirical report of overnight settlement posting for a cash-settled index-option expiry. Independently verified as authentic by the team.
- `AeroQuant_Agent_Architecture_Discussion_v0_2.md` — retired as a standalone document as of this revision; its finalized decisions are folded into §5.4, and its still-open items are carried into §12.
