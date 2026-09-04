# AeroQuant Project Analysis Report Design

## Goal

Produce a team-readable technical audit of the repository, with a PDF deliverable and a Markdown source, covering implementation, runtime flow, trading strategy, measured performance, and material risks.

## Audience and tone

The audience is the whole team, so the report starts with an executive summary and uses plain Indonesian for conclusions while retaining exact English identifiers, formulas, paths, and symbols where precision matters. Findings are labeled as verified behavior, documentation/design intent, observed runtime evidence, or recommendation.

## Scope

- Repository structure and Python dependency stack.
- Runtime entrypoints, scheduled loops, data flow, agents, quant engine, risk gate, execution, monitoring, state, reports, and alerts.
- Actual executable strategy versus archived or aspirational strategy descriptions.
- Existing cycle reports, ledger/database state, tests, and available backtest outputs.
- Security, correctness, operational, and strategy risks.
- Prioritized remediation plan.

## Output structure

1. Executive summary
2. Evidence and methodology
3. Technology stack and repository map
4. Runtime architecture and end-to-end flow
5. Trading strategy analysis
6. Risk, execution, and operational controls
7. Performance and empirical evidence
8. Findings, gaps, and recommendations
9. Appendix: file index, formulas, and verification notes

## Visuals

Include an architecture diagram, a decision-flow diagram, and compact performance/status tables. Visuals must reinforce the implementation evidence and must not imply profitable performance when no closed-trade sample exists.

## Acceptance criteria

- Every material claim cites a repository path, report, database/state observation, or test result.
- Performance separates observed runtime status from backtest results and clearly states when a metric is unavailable.
- The report identifies the current single-leg momentum rail and distinguishes it from older VRP/iron-condor documentation.
- PDF renders successfully and its text is extractable.
- The Markdown source remains available for team review.
