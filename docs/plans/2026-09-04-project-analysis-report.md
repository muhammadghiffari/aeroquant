# Project Analysis Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a verifiable team-facing analysis of the AeroQuant repository in Markdown and PDF.

**Architecture:** Evidence is gathered from source, documentation, tests, state, and reports. The report distinguishes executable behavior from design intent and separates observed performance from unavailable metrics. A static architecture/decision diagram is included in the source and PDF.

**Tech Stack:** Python repository, Markdown, Mermaid or text diagrams, Pandoc/LaTeX or available HTML-to-PDF converter, PyMuPDF/PDF text extraction for validation.

---

### Task 1: Evidence inventory

**Files:**
- Read: `main.py`, `server.py`, `config.py`, `runtime_safety.py`
- Read: `agents/`, `quant_engine/`, `data_engine/`, `execution/`, `evaluation/`, `llm/`, `orchestrator/`
- Read: `tests/`, `reports/`, `state/`, `docs/`

- [ ] Record exact paths and line ranges for stack, flow, strategy, controls, performance, and gaps.
- [ ] Run the repository test suite and inspect available report/state data without exposing secrets.

### Task 2: Draft report source

**Files:**
- Create: `docs/reports/2026-09-04-aeroquant-project-analysis.md`

- [ ] Write the nine approved sections in Indonesian, using evidence citations and explicit confidence labels.
- [ ] Include architecture, decision, and performance visuals/tables.
- [ ] State when closed-trade, win-rate, P/L, or backtest metrics are unavailable.

### Task 3: Export and validate PDF

**Files:**
- Create: `docs/reports/2026-09-04-aeroquant-project-analysis.pdf`

- [ ] Convert the Markdown source with the available local renderer.
- [ ] Extract PDF text, confirm headings and critical findings are present, and inspect page count/file size.

### Task 4: Final review

- [ ] Check for secret leakage, unsupported claims, stale strategy wording, broken references, and missing acceptance criteria.
- [ ] Review `git diff` and `git status`; leave unrelated worktree changes untouched.
