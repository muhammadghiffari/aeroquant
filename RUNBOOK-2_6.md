# AeroQuant — Kickoff-Day Setup Runbook (v4 — reconciled with PRD 2.6)

**Use this top to bottom.** Each section ends with a concrete check — don't move to the next section until the check passes.

**How to read each step:**
- 🔧 **MANUAL** — a human has to click, sign up, or type a secret somewhere. Can't be delegated.
- 🤖 **CLAUDE CODE** — once the manual part (if any) is done, you can literally paste the boxed prompt into Claude Code in that directory and let it do the rest.
- Every command block starts with a one-line header showing **which user** and **which directory** it assumes — run `whoami` and `pwd` first if unsure, don't guess.
- Claude Code already knows the exact model IDs, providers, and non-negotiable rules from `CLAUDE.md` in the repo root — you don't need to re-explain those in your prompts. Just reference "per CLAUDE.md" or "per the PRD" and it'll read them.

**Team:** Ghiffari, Raka, Amil — one Linux user, one Alpaca account, one systemd unit, one git worktree each, running in parallel Mon–Thu (PRD §11b).

---

## 0. 🔧 Credentials inventory (get these open in tabs now)

| Credential | Where | Note |
|---|---|---|
| Anthropic API key | `platform.claude.com` → API Keys → Create Key | **Standalone Console key, NOT your Claude Code login.** Different product surface — see PRD §5.3. |
| Featherless API key | `featherless.ai` → account/dashboard | Confirmed secondary provider (PRD §5.3) — team already holds a paid Developer subscription, and Featherless is a confirmed hackathon tech/prize partner. **Each person gets their own key** (§1c) — don't share one. |
| Alpaca scratch paper account | `app.alpaca.markets` | One per person, for testing only — never submitted |
| Alpaca **official** paper account | Create any time before Monday | One per person, $100,000 exact starting balance — record the account ID the moment it's created |
| VPS SSH access | Your provider's panel (Nevacloud) | Confirm it's the Linux tier, not "RDP" (Windows) |
| GitHub | — | Repo can stay **private** through the whole build week — only needs to be public by submission (PRD §10). Each of the three needs to be added as a collaborator (§4). |
| Telegram | Just have the Telegram app installed | Needed for §10 (BotFather) |

---

## 1. 🔧 VPS: base setup (15 min, once, by whoever has the root password)

```bash
# 👤 as: root  📁 in: (anywhere, right after SSH-ing in)
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git curl ufw fail2ban chrony sqlite3

sudo timedatectl set-timezone UTC
sudo systemctl enable --now chrony
chronyc tracking | grep "Leap status"   # should say "Normal", not "Not synchronised"

sudo ufw allow OpenSSH
sudo ufw --force enable
sudo systemctl enable --now fail2ban
```

**Check:** `chronyc tracking` shows synced.

---

## 2. 🔧 Per-person access and workspace layout (15 min, once)

**Individual Linux users, not shared root.** One per team member:

```bash
# 👤 as: root  📁 in: (anywhere)
sudo adduser ghiffari
sudo adduser raka
sudo adduser amil

# only the admin (whoever holds the VPS root/provisioning responsibility — Ghiffari) gets sudo.
# Raka and Amil do NOT get sudo: with three people sharing one box, sudo is equivalent to root
# for everyone who has it (sudo cat someone else's .env works fine), which defeats per-user file
# permissions entirely. §13 below uses rootless `systemctl --user` units instead, so Raka/Amil
# never need sudo to run their own worker.
sudo usermod -aG sudo ghiffari

# Raka and Amil's user-level systemd units (§13) need "linger" enabled so their processes keep
# running after their SSH session ends — this is a one-time admin action, not something they can
# do themselves without sudo:
sudo loginctl enable-linger raka
sudo loginctl enable-linger amil
```

**On SSH auth method — a real security tradeoff, not just convenience.** Password auth is meaningfully weaker than key-based auth for an internet-facing server, especially once the server's IP has been shared anywhere outside the team (screenshots, chat logs — all count as "shared"). If time allows, have Ghiffari, Raka, and Amil each generate a keypair (`ssh-keygen -t ed25519`) and send you their **public** key (`id_ed25519.pub`) to append to their own `~/.ssh/authorized_keys` — barely more friction than a password, removes a whole attack class. If the team decides password auth is worth the time savings tonight, at minimum use a genuinely strong, unique password per person (not a quick throwaway) and keep root login disabled:

```bash
# 👤 as: root
sudo passwd ghiffari
sudo passwd raka
sudo passwd amil
# set each password interactively when prompted — then tell each person their
# own password directly and privately (DM/call), never in a group chat

sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

Each person then connects with `ssh ghiffari@<server-ip>` (etc.) using their own password — no key exchange needed if you went this route.

**Workspace layout (`git worktree`) — covered at the end of §4, not here.** It needs the GitHub repo
to actually exist first (§4 creates it) — running `git clone --bare <repo-url>` at this point fails
with "repository not found" because there's nothing to clone yet. Finish this section's SSH-login
setup, do §3 (swap) and §4 (repo + collaborators) in order, and the workspace-layout commands are
waiting for you at the bottom of §4.

**Check (for this section only):** each person can `ssh <name>@<server-ip>` in successfully under
their own username and password (or key, if you went that route) — that's all §2 promises. The
worktree/branch check comes at the end of §4.

---

## 3. ⚠️ VPS reality check — actual spec is smaller than earlier planning assumed

**The actually-provisioned VPS is 1 vCPU / 2GB RAM / 30GB NVMe** (confirm your own Nevacloud dashboard) — earlier planning assumed 2 vCPU / 4GB. This matters because three parallel workers (one per person) will run on this same box:

- One Python worker (LangGraph + langchain + Anthropic/OpenAI SDKs + pandas/numpy + FastAPI) realistically sits around 200–400MB RSS.
- Three running concurrently during simultaneous testing can plausibly approach or exceed 2GB — a real OOM-kill risk, not theoretical.
- The pressure is temporary: it's worst *tonight, during simultaneous testing*, eases once the scored week settles into a normal cadence, and disappears once only the submitted account matters.

🔧 **MANUAL — add swap now, it's cheap insurance:**

```bash
# 👤 as: root (or any user with sudo)  📁 in: (anywhere)
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

15 seconds, costs nothing if unused. Skipping it doesn't save meaningful time; an unexplained OOM-kill during tonight's testing costs far more to debug than this takes to prevent.

**Check:** `free -h` shows the 2G swap listed and active.

*(Per-worker `MemoryMax`/`CPUQuota` limits are added to each person's systemd unit in §11 — not for efficiency, but so one worker having a bad moment can't take the other two down with it.)*

---

## 4. 🔧 GitHub repository (10 min, once — do this before anyone starts cloning)

Whoever is repo admin (e.g. Ghiffari):

```bash
# 👤 as: repo admin, on YOUR OWN laptop (not the VPS) — needs `gh` CLI installed and `gh auth login` done once
gh repo create aeroquant --private --description "AeroQuant — autonomous multi-agent options trading agent for the Alpaca AI Trading Agents Hackathon (VRP harvesting via Iron Condors on XSP)" --clone
cd aeroquant
git branch -M main
git push -u origin main
```

If you don't have `gh` installed, the equivalent is: go to github.com → New repository → name `aeroquant` → Private → don't initialize with a README (you'll push your own) → Create.

**Add Raka and Amil as collaborators** (repo stays private through the build — this doesn't make it public, just gives them push access):

```bash
gh repo edit <your-org-or-username>/aeroquant --add-collaborator raka-github-username
gh repo edit <your-org-or-username>/aeroquant --add-collaborator amil-github-username
```

**If Radith ends up running a real trading account (see `ONBOARDING-RADITH.md` §0 — this is a team decision, not settled by default), add him too:**

```bash
gh repo edit <your-org-or-username>/aeroquant --add-collaborator radith-github-username
```

He clones normally on his own laptop (`git clone`, then `git checkout -b strategy/radith`) rather than
using the VPS `git worktree` setup in §4c above — see `ONBOARDING-RADITH.md` for his exact steps.

Or via the web: repo → Settings → Collaborators → Add people.

**Suggested repo description** (used above, and reusable for the submission form's "short description" field later): *"Autonomous multi-agent options trading agent for the Alpaca AI Trading Agents Hackathon — harvests the Volatility Risk Premium via defined-risk Iron Condor spreads on XSP, with a deterministic risk gate and hierarchical LLM reasoning layer."*

**Check:** `gh repo view <org>/aeroquant` shows `Visibility: private` and lists all collaborators — three (Ghiffari, Raka, Amil) or four if Radith is in.

### 4b. GitHub SSH key **from the VPS** — different key from your VPS-login key, easy to conflate

The SSH key discussed in §2 gets you *into the VPS*. It has nothing to do with GitHub. To `git clone`/`push`/`pull` from the VPS, each person needs a **separate** keypair generated **on the VPS itself** (not their laptop), registered to **their own** GitHub account:

```bash
# 👤 as: <name> (ghiffari/raka/amil), on the VPS — once each, after accepting the collaborator invite
ssh-keygen -t ed25519 -C "<name>@aeroquant-vps"
# press Enter through the prompts (default path, no passphrase is fine for this use)
cat ~/.ssh/id_ed25519.pub
```
Copy that output → github.com → Settings → SSH and GPG keys → New SSH key → paste it in. Do this
**before** trying the `git clone --bare` step below, or it fails with a permission/auth error, not a
"repo not found" error (the repo exists by now — this is a different failure mode).

**Check:** `ssh -T git@github.com` (run as that person, on the VPS) replies `Hi <github-username>! You've successfully authenticated`.

### 4c. Workspace layout — `git worktree`, not one shared directory (do this now, repo exists)

A single shared clone doesn't work for three people on three branches (git only checks out one branch per working directory at a time). `git worktree` shares one `.git` object store across independent working directories — nothing duplicated on disk, but each person's directory is genuinely their own:

```bash
# 👤 as: ghiffari (do this once, after HIS OWN 4b key is registered)  📁 in: ~
git clone --bare git@github.com:<org-or-username>/aeroquant.git ~/aeroquant.git
```

Then **each person, once their own 4b key is registered**, logged in as themselves:

```bash
git worktree add -b strategy/ghiffari ~/aeroquant-ghiffari    # Ghiffari runs this
git worktree add -b strategy/raka     ~/aeroquant-raka        # Raka runs this
git worktree add -b strategy/amil     ~/aeroquant-amil        # Amil runs this
```

Note the `-b` flag — without it, this fails if the branch doesn't already exist yet, which it won't the first time.

Each person now works inside `~/aeroquant-<their-name>/` as if it were a normal independent clone — `git status`, `git add`, `git commit`, `git push` all behave normally, while the Risk Gate and other shared code stay diffable across worktrees against one true `.git` history.

**Check:** each person's `git status` inside their own `~/aeroquant-<name>/` shows their own branch, and `git worktree list` (run by anyone on the VPS) shows all worktrees pointing at the one shared `~/aeroquant.git`.

---

## 5. Per-person repo clone and environment (repeat ×3, one per person, on the VPS)

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/   (created by the worktree command in §4c — cd into it first)
cd ~/aeroquant-ghiffari
python3.11 -m venv .venv
source .venv/bin/activate
```

🤖 **CLAUDE CODE — from here, this whole block can just be a prompt** (run `claude` inside `~/aeroquant-ghiffari/` first, with the venv activated):

> Create a `requirements.txt` with: langgraph, langchain-anthropic, langchain-openai, anthropic, alpaca-py, pydantic>=2, fastapi, uvicorn, numpy, pandas, python-dotenv, httpx. Install them. Then create the directory structure `agents/ orchestrator/ execution/ data_engine/ evaluation/ state/ reports/ tests/`, and a `.gitignore` covering `.env`, `.venv/`, `__pycache__/`, `*.db*`, `state/*.json`, `reports/*.json`. Read `CLAUDE.md` and `AeroQuant-VRP-Harvester-PRD-2_6.md` in the repo root first so you know the architecture before creating anything.

If doing it by hand instead:

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/  (venv active)
cat > requirements.txt << 'EOF'
langgraph
langchain-anthropic
langchain-openai
anthropic
alpaca-py
pydantic>=2
fastapi
uvicorn
numpy
pandas
python-dotenv
httpx
EOF
pip install -r requirements.txt

mkdir -p agents orchestrator execution data_engine evaluation state reports tests
touch .env .env.example

cat > .gitignore << 'EOF'
.env
.venv/
__pycache__/
*.db
*.db-wal
*.db-shm
state/*.json
reports/*.json
EOF
```

`.env.example` (commit this — no real secrets, just the shape):

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/
cat > .env.example << 'EOF'
ANTHROPIC_API_KEY=sk-ant-xxxxx
FEATHERLESS_API_KEY=fw-xxxxx
APCA_API_KEY_ID=PKxxxxx
APCA_API_SECRET_KEY=xxxxx
APCA_API_BASE_URL=https://paper-api.alpaca.markets
ALPACA_ACCOUNT_ID=xxxxx
ALPACA_PAPER_TRADE=true
ENVIRONMENT_ID=scratch-ghiffari
EOF
```

Now put **real scratch-account** values into `.env` (never `.env.example`) — `ENVIRONMENT_ID=scratch-ghiffari`, Ghiffari's own scratch Alpaca keys, Ghiffari's own Anthropic and Featherless keys. **`BYTEPLUS_*` vars are deliberately not included** — BytePlus Ark is not part of the confirmed provider decision (PRD §5.3/§12).

**Check:** `git status` shows `.env` is ignored, not tracked. **Repeat this entire section for Raka (`~/aeroquant-raka/`, `ENVIRONMENT_ID=scratch-raka`) and Amil (`~/aeroquant-amil/`, `ENVIRONMENT_ID=scratch-amil`).**

---

## 6. Day-0 reachability smoke test (2 min, per person)

```bash
# 👤 as: ghiffari (repeat for raka, amil)  📁 in: anywhere
curl -sS -o /dev/null -w "Alpaca paper API: %{http_code}\n" https://paper-api.alpaca.markets/v2/clock
curl -sS -o /dev/null -w "BluePack API:    %{http_code}\n" https://ai.bluepack.my.id/anthropic
curl -sS -o /dev/null -w "Featherless API:  %{http_code}\n" https://api.featherless.ai/v1
```

**Check:** all three return an HTTP status (401/403 is fine — reachable but unauthenticated; a timeout means the VPS's outbound network is blocked — sort that with Nevacloud before anything else).

---

## 7. BluePack / `model_gateway.py` smoke test (5 min, per person)

**BluePack (`https://ai.bluepack.my.id/anthropic`) is the adopted primary Anthropic-compatible endpoint** (PRD §5.3, adopted 2026-08-31). This test verifies it end-to-end before any scored trading.

Set `ANTHROPIC_BASE_URL=https://ai.bluepack.my.id/anthropic` in your `.env` alongside your `ANTHROPIC_API_KEY`.

🤖 **CLAUDE CODE prompt** (run inside your own `~/aeroquant-<name>/`, venv active, `.env` filled in):

> Write `tests/smoke_anthropic.py` that loads `.env`, calls both `claude-haiku-4-5-20251001` and `claude-sonnet-5` via `model_gateway.py`'s `ModelGateway` with the `fast_analysis` policy, using a one-line "reply with exactly: OK" prompt, and prints the reply plus the provider name returned. Then run it.

**Check:** both models reply `OK` via BluePack with nonzero token counts. Auth error → double-check the API key is correct and `ANTHROPIC_BASE_URL` is set. 429 → note your rate-limit tier now.

---

## 8. Featherless / `model_gateway.py` smoke test (per person, before Monday)

Copy `model_gateway.py` into your own worktree root first (`~/aeroquant-<name>/model_gateway.py`) if it isn't there via git yet.

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/  (venv active, .env filled in)
python model_gateway.py
```

**Check:** every policy prints `served by bluepack-coding-...` under normal conditions (BluePack is the adopted primary; PRD §5.3). To confirm the Featherless fallback itself works:

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/
ANTHROPIC_API_KEY=sk-ant-invalid-for-testing python model_gateway.py
```

You should see it fail over to `featherless-qwen3-32b` instead of raising `RuntimeError: All providers exhausted`. This only overrides the env var for this one process — your real key in `.env` is untouched.

**Before considering the GLM candidate (PRD §5.3) for anything beyond a documented pending item**, run the same forced test but check the reply parses correctly:

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/
ANTHROPIC_API_KEY=sk-ant-invalid-for-testing python -c "
from model_gateway import ModelGateway
from pydantic import BaseModel, Field

class Ping(BaseModel):
    reply: str = Field(description='Should be exactly OK')

g = ModelGateway()
parsed, provider = g.generate(
    role='glm_verification', policy='fast_analysis',
    messages=[{'role': 'user', 'content': 'Reply with exactly: OK'}],
    response_model=Ping, correlation_id='glm-verify',
)
print(f'{provider}: {parsed.reply}')
"
```

To specifically test GLM rather than Qwen3, temporarily edit `_build_role_chains()`'s `fast_analysis` list to swap in `_featherless("zai-org/GLM-4.7-Flash")` before running this — revert if it fails, only make it the real default once it passes cleanly for whoever tests it.

---

## 9. 🔧 Alpaca Skills (10 min, per person — best-practice Claude Code setup)

Alpaca publishes real, official Claude Code skills at `github.com/alpacahq/alpaca-skills` — step-by-step instructions Claude Code follows automatically for common Alpaca tasks. Currently one is published: `alpaca-trading-backtest`, directly relevant since backtesting/validation is already part of §14's work.

```bash
# 👤 as: ghiffari (repeat for raka, amil)  📁 in: ~
# 1. Install the Alpaca CLI (prerequisite for the skill)
go install github.com/alpacahq/cli/cmd/alpaca@latest
# or: brew install alpacahq/tap/cli   (if Homebrew is available on the VPS)

alpaca profile login   # or set ALPACA_API_KEY / ALPACA_SECRET_KEY directly

# 2. Pull the skill into Claude Code's skill directory
git clone https://github.com/alpacahq/alpaca-skills.git /tmp/alpaca-skills
mkdir -p ~/.claude/skills
cp -r /tmp/alpaca-skills/skills/trading-api/backtest ~/.claude/skills/alpaca-trading-backtest
```

Claude Code will now pick this up automatically — no per-prompt setup needed. Check `github.com/alpacahq/alpaca-skills` again closer to Wednesday/Thursday in case more skills get published (e.g. for options-chain fetching or live order placement) — it's a young, actively-scoped repo.

**Check:** ask Claude Code "what Alpaca skills do you have access to?" — it should mention the backtesting skill.

---

## 10. 🔧 Alpaca MCP server (10 min, per person)

```bash
# 👤 as: ghiffari (repeat for raka, amil)  📁 in: ~/aeroquant-ghiffari/
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs   # only needs to happen once system-wide, harmless to re-run

npx -y @alpacahq/alpaca-mcp-server --help

echo 'ALPACA_TOOLSETS=account,trading,assets,options-data,stock-data,index-data,news' >> .env
```

Expect `index-data` calls to fail or return empty (`get_index_latest_values`/`get_index_values`) — a known, still-open Alpaca gap, not a setup mistake. `options-data` rides OPRA separately and should work fine.

**Check:** run one `get_option_contracts` call against XSP through the MCP server and confirm you get real contracts back.

---

## 11. Deterministic core: build and unit-test first, before any LLM wiring — COMPLETE

**Status — COMPLETE:** Quant Engine, Risk Manager, and ForceCloseGuard are implemented. The deterministic core has been committed, and the unit test suite is green with **138 passing tests**.

🤖 **CLAUDE CODE prompt** (per person, inside their own worktree — this is the bulk of Day 0/1 build work):

> Read `AeroQuant-VRP-Harvester-PRD-2_6.md` §5.1 and §8, and `CLAUDE.md`, then build in this order: (1) `data_engine/quant_engine.py` computing HV30, IV Rank/Percentile, Expected Move, skew, and 20-day momentum Z-score, with unit tests against a fixture CSV that need no network access; (2) `agents/risk_manager_agent.py` enforcing PRD §8's sizing, spread-width, liquidity, exposure, and kill-switch rules, with a test that deliberately breaches each limit and confirms rejection; (3) a scheduled force-close-before-expiry job per PRD §9, tested against the scratch account. Follow every non-negotiable constraint in `CLAUDE.md` — especially that the Risk Gate is the only path to order submission.

Once that's built:

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/  (venv active)
pytest
```

**Validate SL/TP before trusting it** — wire `win_rate_validator.py`'s `load_data()` to your real Day-0 EOD/IV pull (it deliberately raises `NotImplementedError` until you do this), then:

```bash
# 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/
python win_rate_validator.py
```

Compare every `(dte, em_mult)` row's empirical win rate against the printed breakeven threshold for the current §8 setting (TP=50%, SL=125%). If nothing clears breakeven at your current strike-placement logic, widen strike placement before loosening SL further. This script checks terminal price-at-DTE, not path-dependent TP/SL triggers — a sanity check on strike placement, not a literal live-exit simulation.

**Check:** `pytest` green on all three deterministic pieces, run against your scratch account at least once; `win_rate_validator.py` output compared against breakeven before finalizing §8's numbers anywhere in the pitch deck.

---

## 11b. Execution integration — COMPLETE

**Status — COMPLETE:**

- Deterministic Alpaca broker adapter implemented; alpaca-py 0.44.0 compatibility verified.
- Typed, atomic MLEG entry requests enforce LIMIT-only execution, signed debit/credit pricing, account scoping, and restart-safe `client_order_id` idempotency.
- Broker uncertainty fails closed; the deterministic force-close execution path and `OrderIntent` expiry enforcement are implemented.
- Read-only Alpaca Paper integration completed: 0 positions and 0 open orders were observed; no order submission or cancellation was performed.
- Execution integration committed as `10fa950`.

---

## 12. LangGraph wiring

**Current verified status — IN PROGRESS; do not mark this section COMPLETE.**

- Durable LangGraph checkpointing is verified: `compile_graph()` now defaults to the official disk-backed `SqliteSaver` at `state/langgraph_checkpoints.db`, with an optional explicit SQLite path for operational/test isolation. New graph/checkpointer instances recover the same account-scoped thread from disk, and a separate-process test writes in process A and recovers in process B.
- The installed official saver creates its database schema with SQLite WAL mode; the real persistence test queries `PRAGMA journal_mode` and verifies `wal` on the resulting file.
- Persisted checkpoints use `JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None)`, so pickle and arbitrary application-module deserialization are disabled. Recovered data is validated through `CycleState` at the graph boundary.
- There is no implemented deterministic XSP contract-selection/CandidateBuilder in this repository. The default boundary therefore emits an explicit `CANDIDATE_NOT_READY` safe rejection; it does not fabricate option symbols, strikes, expiries, quotes, account context, or an executable intent.
- Still unresolved: deterministic XSP contract selection/CandidateBuilder, a concrete production `CyclePersistence` store beyond checkpoints, functional XSP `get_option_contracts` MCP check, embedding-provider decision, and the MCP-compliance interpretation for the EvidenceGateway pattern.

🤖 **CLAUDE CODE prompt:**

> Read `AeroQuant-VRP-Harvester-PRD-2_6.md` §5.4 for the full node/edge structure (`precheck → evidence → quant → candidates → memory → [volatility ‖ macro] → technical → [bull ‖ bear] → chief → validator → risk_gate → persist`), and `CLAUDE.md` for the role-policy-to-model mapping. Build this as a checkpointed LangGraph `StateGraph`. Two things to get right: the checkpointer's `thread_id` must be `f"{alpaca_account_id}:{cycle_id}"`, not just `cycle_id`; and the `chief ↔ proposal_validator` repair loop needs an explicit hop counter in `CycleState` so a bad proposal can't loop forever in one cycle. Every LLM-calling node must go through `model_gateway.py`'s `ModelGateway.generate(...)` — never a raw client instantiated inline.

**Check:** kill the worker process mid-cycle (`kill -9`), restart it, confirm the graph resumes from its last checkpoint instead of restarting or losing state.

---

## 13. 🔧 Process supervision (systemd) — one unit per person

```bash
# 👤 as: ghiffari, with sudo  📁 in: ~/aeroquant-ghiffari/
sudo tee /etc/systemd/system/aeroquant-ghiffari.service > /dev/null << 'EOF'
[Unit]
Description=AeroQuant trading worker — Ghiffari
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ghiffari
WorkingDirectory=/home/ghiffari/aeroquant-ghiffari
EnvironmentFile=/home/ghiffari/aeroquant-ghiffari/.env
ExecStart=/home/ghiffari/aeroquant-ghiffari/.venv/bin/python -m orchestrator.main --loop
Restart=always
RestartSec=10
MemoryMax=600M
CPUQuota=80%
StandardOutput=append:/home/ghiffari/aeroquant-ghiffari/logs/worker.log
StandardError=append:/home/ghiffari/aeroquant-ghiffari/logs/worker-error.log

[Install]
WantedBy=multi-user.target
EOF

mkdir -p ~/aeroquant-ghiffari/logs
sudo systemctl daemon-reload
sudo systemctl enable aeroquant-ghiffari
```

**Raka and Amil use a rootless `systemctl --user` unit instead** — not a system unit, since §2 no longer
grants them sudo (sudo would be equivalent to root for whoever holds it, which defeats per-user file
permissions on a shared box). Same shape, different mechanism:

```bash
# 👤 as: raka (or amil), NO sudo  📁 in: ~/aeroquant-raka/
mkdir -p ~/.config/systemd/user
tee ~/.config/systemd/user/aeroquant-raka.service > /dev/null << 'EOF'
[Unit]
Description=AeroQuant trading worker — Raka
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/raka/aeroquant-raka
EnvironmentFile=/home/raka/aeroquant-raka/.env
ExecStart=/home/raka/aeroquant-raka/.venv/bin/python -m orchestrator.main --loop
Restart=always
RestartSec=10
MemoryMax=600M
CPUQuota=80%
StandardOutput=append:/home/raka/aeroquant-raka/logs/worker.log
StandardError=append:/home/raka/aeroquant-raka/logs/worker-error.log

[Install]
WantedBy=default.target
EOF

mkdir -p ~/aeroquant-raka/logs
systemctl --user daemon-reload
systemctl --user enable aeroquant-raka
```

Two differences from Ghiffari's unit above: no `User=` line (implicit in a user unit — writing it
errors), and `WantedBy=default.target` not `multi-user.target` (that target only exists for system
units). This requires `sudo loginctl enable-linger raka` to already have been run by Ghiffari in §2,
or the unit stops the moment the SSH session ends. Amil repeats this with his own username substituted
everywhere (`aeroquant-amil.service`, `/home/amil/aeroquant-amil` paths).

Don't `start`/`enable --now` any of the three yet — still on scratch accounts.

```bash
# 👤 as: ghiffari, with sudo — logrotate stays root-managed regardless of unit type;
# reading another user's log file to rotate it is a different threat than sudo-cat'ing their .env
sudo tee /etc/logrotate.d/aeroquant-ghiffari > /dev/null << 'EOF'
/home/ghiffari/aeroquant-ghiffari/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
EOF
# repeat for /home/raka/aeroquant-raka/logs/*.log and /home/amil/aeroquant-amil/logs/*.log
```

**Check:** `sudo systemctl status aeroquant-ghiffari` shows it enabled (not started). `systemctl --user status aeroquant-raka` / `aeroquant-amil` (run by each of them, no sudo) shows the same for theirs. `systemctl --user show aeroquant-raka -p MemoryMax` should show the actual byte value, not `infinity` — if it shows `infinity`, cgroup delegation isn't active and needs checking before relying on it for OOM protection. `free -h` still shows healthy headroom with all three units present but stopped.

---

## 14. 🔧 Alerting via Telegram (10 min, once — shared across all three, or one bot per person, team's choice)

Yes, this really is this easy — step by step:

1. Open Telegram (phone or desktop app). In the search bar, type `@BotFather` and open the official one (blue checkmark).
2. Send `/newbot`. It asks for a **display name** (anything, e.g. "AeroQuant Alerts") — type it and send.
3. It then asks for a **username**, which must end in `bot` (e.g. `aeroquant_alerts_bot`) and be globally unique on Telegram — if it says taken, try another. Send it.
4. BotFather replies with a message containing your **bot token** — a long string like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`. Copy it.
5. Now open a chat with **your own new bot** (search its username, same as searching any contact) and send it any message, e.g. "hi" — this is required so Telegram knows to let the bot message you back.
6. Find your **chat ID** by running this from any machine with `curl`:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN_HERE>/getUpdates"
   ```
   Look for `"chat":{"id":123456789,...}` in the JSON response — that number is your `chat_id`.
7. Add both to `.env` (each person's own, if going with one bot per person; or shared if the team prefers one alert channel):
   ```bash
   echo 'TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ' >> .env
   echo 'TELEGRAM_CHAT_ID=123456789' >> .env
   ```

🤖 **CLAUDE CODE prompt** (once the token/chat_id above are in `.env`):

> Write a small `alerts.py` helper that sends a Telegram message via `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from `.env`, and wire it to fire on: kill-switch trigger, process crash (a systemd `OnFailure=` unit calling it), MCP failure, account-ID mismatch at startup, and `model_gateway.py` falling back off Anthropic.

**Check:** send yourself one test message from the worker's alert function before relying on it.

---

## 15. Streamlit/Replit/Vercel demo — optional (team's choice, not a hackathon requirement)

The official FAQ confirms: *"a hosted link is needed only if the submission includes a demo app that judges must open."* The team has chosen to build one anyway for the Presentation & Execution story — that's a legitimate choice, just not compliance-required.

Separate deployment from the VPS — the VPS's FastAPI stays private, a thin public app calls it over HTTPS.

- Pick one of the three (Streamlit is usually fastest).
- Deploy a placeholder that hits a health-check endpoint on your VPS's FastAPI.
- Confirm the URL is publicly reachable — this becomes the submission's Application URL.

**Check:** the public URL loads from a phone on mobile data.

---

## 16. Account cutover — Monday, Aug 31, 9:30 AM ET (×3 on the VPS, one per person — PRD §11b; plus Radith's own account on his laptop, if he's running one — see `ONBOARDING-RADITH.md` §0)

Each person, on their own official account and their own `aeroquant-<name>.service`:

1. **Now through the weekend:** keep running against your own **scratch** account. Don't touch the official one yet.
2. **Any time before Monday:** create **your own** official dedicated $100,000 Alpaca paper account. Record its account ID. Don't trade on it.
3. **Monday, Aug 31, 9:30 AM ET, exactly:**
   ```bash
   # 👤 as: ghiffari  📁 in: ~/aeroquant-ghiffari/
   # edit .env: swap APCA_*, ALPACA_ACCOUNT_ID, ENVIRONMENT_ID=official-ghiffari
   sudo systemctl start aeroquant-ghiffari
   ```
   This is **your** first scored cycle. Watch its logs end to end.
4. **Repo** stays private through this — just don't forget to flip it public before submission Friday.

**Check:** each person's `reports/` shows a complete cycle with their own correct `alpaca_account_id`, timestamp reading Monday.

---

## 17. ⚠️ Thursday close — the deadline that actually determines your score (applies to every account the team is running — not just whichever gets submitted)

The official FAQ's own text is internally inconsistent about the exact P&L-snapshot instant (Thursday EOD vs. a Friday 9:30 AM ET raw snapshot). The adopted posture — flat well before Thursday's close — is safe under every reading in circulation. **This applies independently to all three VPS accounts, not just whichever one gets submitted Friday — and to Radith's account too if he's running a real one on his own infra, for the identical reason** — the comparison happens after this deadline, and nobody knows Friday morning which account wins.

1. **Thursday morning:** trade normally, each account on its own cadence.
2. **Thursday early-to-mid afternoon:** each person stops opening new positions. Give every open position time to close via a normal limit order well before the close.
3. **Thursday, by 4:15 PM ET at the absolute latest (aim earlier):** confirm zero open positions on **all three** accounts.
4. **Friday morning:** compare all three accounts' total equity, pick the best, submit that account's ID. No scored trading happens Friday regardless. Reconfirm the actual submission-cutoff time on the live countdown before Friday — a team screenshot shows "10:00 PM WIT" (13:00 UTC), which doesn't match the 15:00 UTC figure used everywhere else; most likely a WIB/WIT mislabel, but check directly rather than assume.

**Check:** by end of day Thursday, `reports/` shows zero open positions for every one of the three accounts, and every closed position closed via an explicit close order — never a natural expiration.

---

## 18. Standing daily routine (repeat every morning, Mon Aug 31 – Thu Sep 3, ×3)

- [ ] `status.alpaca.markets` — check for incidents before assuming a bug is yours
- [ ] `docs.alpaca.markets/us/changelog` — re-check for a settlement-bug fix
- [ ] Manual settlement-verification check: open one small OTM short, let it expire, diff Alpaca's reported cash flow and its timing against the correct intrinsic-value calc
- [ ] `chronyc tracking` — confirm the VPS clock hasn't drifted
- [ ] `df -h` and `free -h` — confirm disk/swap headroom is holding across all three workers
- [ ] **On Thursday specifically:** treat this as a pre-close-out check — see §17.

---

## 19. 🔧 Pre-kickoff work disclosure (mandatory deliverable)

Confirmed mandatory per the official FAQ: the README or one-page write-up must state plainly what was built **before** Aug 28 versus **during** the official window. This is a documentation task, easy to forget under time pressure — draft it now, don't leave it for Thursday night. `README.md`'s "Pre-kickoff work disclosure" section already has the placeholder; fill it in honestly per person/per component.

**Check:** the one-page write-up has an explicit sentence or two on pre-kickoff work before you consider it done.

---

**If something in this runbook contradicts what you actually build** (a script name, a module path), that's expected — this maps to the PRD's proposed structure, not a byte-for-byte spec. The checks at the end of each section are the part that should hold regardless of exact implementation.