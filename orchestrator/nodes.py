"""
orchestrator/nodes.py — All LangGraph node implementations.

Design rules:
  - Every LLM-calling node uses ModelGateway.generate().
  - Deterministic nodes call existing modules directly.
  - Nodes are pure functions: state -> state (or raise on fatal error).
  - No node ever instantiates an Alpaca client or calls trading APIs.
  - Error paths return state with phase=FAILED rather than raising.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING


from model_gateway import ModelGateway
from orchestrator.state import (
    MAX_REPAIR_HOPS,
    BullBearReport,
    ChiefReport,
    CyclePhase,
    CycleState,
    MacroReport,
    TechnicalReport,
    ValidatorReport,
    VolatilityReport,
)

if TYPE_CHECKING:
    from data_engine.quant_engine import EvidenceEnvelope, MarketDataBundle, QuantMetrics
    from agents.risk_manager import RiskDecision, RiskManager, TradeProposal
    from orchestrator.boundaries import (
        CandidateBuilder,
        EvidenceGatherer,
        SemanticMemory,
        TradingMandate,
    )

logger = logging.getLogger("aeroquant.nodes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MG = None  # Lazy-initialized ModelGateway singleton


def _mg() -> ModelGateway:
    global _MG
    if _MG is None:
        _MG = ModelGateway()
    return _MG


def _llm_call(
    node_name: str,
    policy: str,
    messages: list[dict],
    response_model,
    correlation_id: str,
) -> tuple[object, str]:
    """
    Thin wrapper around ModelGateway.generate() for consistent error handling.
    Returns (parsed_response, provider_name).
    Raises on failure — callers must catch and return FAILED state.
    """
    mg = _mg()
    return mg.generate(
        role=node_name,
        policy=policy,  # type: ignore[arg-type]
        messages=messages,
        response_model=response_model,
        correlation_id=correlation_id,
    )


def _fail_state(
    state: CycleState,
    node: str,
    error: str,
) -> CycleState:
    """Return a failure state — used by every node on error."""
    return state.model_copy(
        deep=True,
        update={
            "phase": CyclePhase.FAILED,
            "failed_node": node,
            "error": error,
        }
    )


# ---------------------------------------------------------------------------
# 1. precheck
# ---------------------------------------------------------------------------

def node_precheck(
    state: CycleState,
    *,
    mandate,  # TradingMandate — injected at graph build time
) -> CycleState:
    """
    Precheck: validate PRD/Runbook prerequisites before starting a cycle.

    Checks:
      - Account ID is set and non-empty.
      - Mandate allows trading right now.
      - No global kill switch is active (checked via mandate).

    This node does NOT make trading decisions — it only validates
    that the prerequisites for attempting a cycle are met.
    """
    try:
        if not state.alpaca_account_id:
            return _fail_state(
                state, "precheck", "alpaca_account_id is empty — cannot start cycle"
            )

        allowed, reason = mandate.is_trading_allowed()
        if not allowed:
            return state.model_copy(
                deep=True,
                update={
                    "phase": CyclePhase.PRECHECK,
                    "precheck_passed": False,
                    "precheck_reason": f"Trading not allowed: {reason}",
                }
            )

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.PRECHECK,
                "precheck_passed": True,
                "precheck_reason": reason,
            }
        )
    except Exception as exc:
        return _fail_state(state, "precheck", f"Precheck error: {exc}")


# ---------------------------------------------------------------------------
# 2. evidence
# ---------------------------------------------------------------------------

_MAX_EVIDENCE_AGE_SECONDS = 5 * 60


def _parse_snapshot_timestamp(value: object) -> datetime:
    """Validate freshness without manufacturing a collection time."""
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("evidence is missing snapshot_timestamp")

    if timestamp.tzinfo is None:
        raise ValueError("evidence snapshot_timestamp must be timezone-aware")

    timestamp = timestamp.astimezone(timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if age_seconds < -60:
        raise ValueError("evidence snapshot_timestamp is implausibly in the future")
    if age_seconds > _MAX_EVIDENCE_AGE_SECONDS:
        raise ValueError(
            f"evidence is stale ({age_seconds:.0f}s old; max "
            f"{_MAX_EVIDENCE_AGE_SECONDS}s)"
        )
    return timestamp


def _assert_finite_bundle(bundle) -> None:
    """Reject NaN/Infinity before data becomes agent evidence."""
    for field in ("spot_price", "atm_call_price", "atm_put_price", "iv_atm", "iv_25_delta_put"):
        value = getattr(bundle, field)
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"evidence {field} is non-finite")

    for field in ("historical_closes", "historical_iv"):
        series = getattr(bundle, field)
        if series.empty:
            raise ValueError(f"evidence {field} is empty")
        if not all(math.isfinite(float(value)) for value in series):
            raise ValueError(f"evidence {field} contains non-finite values")


def _serialized_series(series) -> list[dict[str, object]]:
    return [
        {"timestamp": timestamp.isoformat(), "value": float(value)}
        for timestamp, value in series.items()
    ]


def _serialize_evidence_bundle(bundle, snapshot_timestamp: datetime, raw: dict) -> dict:
    """Store only checkpoint-safe, lossless data-plane values in CycleState."""
    return {
        "symbol": bundle.symbol,
        "spot_price": bundle.spot_price,
        "historical_closes": _serialized_series(bundle.historical_closes),
        "historical_iv": _serialized_series(bundle.historical_iv),
        "atm_call_price": bundle.atm_call_price,
        "atm_put_price": bundle.atm_put_price,
        "iv_atm": bundle.iv_atm,
        "iv_25_delta_put": bundle.iv_25_delta_put,
        "dte": bundle.dte,
        "open_interest": bundle.open_interest,
        "iv_quality": bundle.iv_quality.value,
        "spot_quality": bundle.spot_quality.value,
        "chain_quality": bundle.chain_quality.value,
        "snapshot_timestamp": snapshot_timestamp.isoformat(),
        # Retain only non-execution qualitative context for the macro node.
        "news": raw.get("news", []) if isinstance(raw.get("news", []), list) else [],
        # Do not imply that indicative options data is OPRA/execution-grade.
        "options_data_tier": raw.get("options_data_tier", raw.get("options_feed", "unspecified")),
    }


def _bundle_from_state_evidence(evidence: dict):
    """Reconstruct MarketDataBundle from the checkpoint-safe evidence form."""
    from data_engine.quant_engine import MarketDataBundle
    import pandas as pd

    def decode_series(value, field: str):
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, dict) and "timestamp" in item and "value" in item for item in value)
        ):
            index = pd.DatetimeIndex(pd.to_datetime([item["timestamp"] for item in value], utc=True))
            return pd.Series([item["value"] for item in value], index=index)
        return value

    payload = dict(evidence)
    for field in ("historical_closes", "historical_iv"):
        payload[field] = decode_series(payload.get(field), field)
    for field in ("snapshot_timestamp", "news", "options_data_tier"):
        payload.pop(field, None)
    return MarketDataBundle.model_validate(payload)


def _fallback_tier(bundle) -> str:
    from data_engine.quant_engine import DataQuality

    qualities = (bundle.iv_quality, bundle.spot_quality, bundle.chain_quality)
    if DataQuality.UNAVAILABLE in qualities:
        raise ValueError("evidence declares an unavailable required data source")
    if DataQuality.STALE in qualities:
        return "stale"
    if DataQuality.ESTIMATED in qualities:
        return "estimated"
    return "primary"


def _assert_finite_metrics(metrics) -> None:
    for field in (
        "hv30", "iv_rank", "iv_percentile", "em_pct", "momentum_zscore_20d", "hv_vs_iv_ratio",
    ):
        value = getattr(metrics, field)
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"QuantEngine produced non-finite {field}")


def node_evidence(
    state: CycleState,
    *,
    gatherer,  # EvidenceGatherer
    symbol: str = "XSP",
) -> CycleState:
    """Gather and validate real market evidence; missing/stale data fails closed."""
    if state.is_failure():
        return state

    try:
        from data_engine.quant_engine import DataQuality

        raw = gatherer.gather(symbol=symbol, bundle_type="snapshot")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("no market evidence returned")

        bundle = _bundle_from_state_evidence(raw)
        _assert_finite_bundle(bundle)
        snapshot_timestamp = _parse_snapshot_timestamp(raw.get("snapshot_timestamp"))
        if DataQuality.UNAVAILABLE in (
            bundle.iv_quality,
            bundle.spot_quality,
            bundle.chain_quality,
        ):
            raise ValueError("evidence declares an unavailable required data source")
        if DataQuality.STALE in (
            bundle.iv_quality,
            bundle.spot_quality,
            bundle.chain_quality,
        ):
            raise ValueError("evidence declares stale required data")

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.EVIDENCE,
                "evidence": _serialize_evidence_bundle(bundle, snapshot_timestamp, raw),
            },
        )
    except Exception as exc:
        logger.warning("Evidence gathering rejected: %s", exc)
        return _fail_state(state, "evidence", f"Evidence error: {exc}")


# ---------------------------------------------------------------------------
# 3. quant
# ---------------------------------------------------------------------------

def node_quant(
    state: CycleState,
    *,
    engine,  # QuantEngine — the existing deterministic engine
    evidence: dict | None = None,
) -> CycleState:
    """Compute metrics with QuantEngine only; never synthesize market inputs."""
    if state.is_failure():
        return state

    try:
        from data_engine.quant_engine import EvidenceEnvelope

        ev = state.evidence if evidence is None else evidence
        if not isinstance(ev, dict) or not ev:
            raise ValueError("no validated evidence available for QuantEngine")

        bundle = _bundle_from_state_evidence(ev)
        _assert_finite_bundle(bundle)
        metrics = engine.compute(bundle)
        _assert_finite_metrics(metrics)
        tier = _fallback_tier(bundle)
        quality_flags = [f"data_quality:{tier}"] if tier != "primary" else []
        data_tier = ev.get("options_data_tier")
        if isinstance(data_tier, str) and "indicative" in data_tier.lower():
            quality_flags.append("options_data:indicative_signal_only")

        snapshot_value = ev.get("snapshot_timestamp")
        snapshot_timestamp = (
            _parse_snapshot_timestamp(snapshot_value)
            if snapshot_value is not None
            else datetime.now(timezone.utc)
        )
        envelope = EvidenceEnvelope(
            metrics=metrics,
            source_symbol=bundle.symbol,
            snapshot_timestamp=snapshot_timestamp,
            fallback_tier=tier,
            quality_flags=quality_flags,
        )

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.QUANT,
                "quant_metrics": metrics,
                "evidence_envelope": envelope,
            },
        )
    except Exception as exc:
        return _fail_state(state, "quant", f"QuantEngine error: {exc}")


# ---------------------------------------------------------------------------
# 4. candidates
# ---------------------------------------------------------------------------

def _assert_candidate_market_data(candidate) -> None:
    """Reject stale, non-finite, inverted, or inconsistent option quotes."""
    proposal = candidate.proposal
    if proposal.timestamp.tzinfo is None:
        raise ValueError("candidate proposal timestamp must be timezone-aware")
    for leg in proposal.spread.legs:
        values = {
            "strike": leg.strike,
            "bid": leg.bid_price,
            "ask": leg.ask_price,
            "mid": leg.mid_price,
        }
        if not all(math.isfinite(float(value)) for value in values.values()):
            raise ValueError(f"candidate leg {leg.symbol} contains non-finite market data")
        if leg.bid_price > leg.ask_price:
            raise ValueError(f"candidate leg {leg.symbol} has inverted bid/ask")
        if leg.ask_price <= 0 or leg.mid_price <= 0:
            raise ValueError(f"candidate leg {leg.symbol} has non-positive quote")
        quoted_mid = (leg.bid_price + leg.ask_price) / 2
        if not math.isclose(leg.mid_price, quoted_mid, rel_tol=0.05, abs_tol=0.01):
            raise ValueError(f"candidate leg {leg.symbol} has inconsistent bid/ask/mid")
        if leg.expiration.tzinfo is None:
            raise ValueError(f"candidate leg {leg.symbol} expiration must be timezone-aware")


def node_candidates(
    state: CycleState,
    *,
    builder,  # CandidateBuilder
    mandate,  # TradingMandate
) -> CycleState:
    """Obtain real, provenance-bearing candidates through CandidateBuilder.

    CandidateBuilder is the only graph boundary allowed to supply a
    ``TradeProposal``.  An absent implementation is an explicit NOT_READY
    condition, not an excuse to synthesize contracts, prices, or account data.
    """
    if state.is_failure():
        return state

    try:
        from orchestrator.state import CandidateReadiness, ValidatedCandidate

        candidates = builder.build(state=state, mandate=mandate)
        if not isinstance(candidates, list):
            raise ValueError("CandidateBuilder must return list[ValidatedCandidate]")
        if not candidates:
            return state.model_copy(
                deep=True,
                update={
                    "phase": CyclePhase.CANDIDATES,
                    "candidates": [],
                    "candidate_readiness": CandidateReadiness.NOT_READY,
                    "candidate_reason": (
                        "No validated options-chain candidate is available; "
                        "contract selection is not ready"
                    ),
                    "risk_proposal": None,
                },
            )

        evidence_symbol = str(state.evidence.get("symbol", ""))
        for candidate in candidates:
            if not isinstance(candidate, ValidatedCandidate):
                raise ValueError("CandidateBuilder returned a non-validated candidate")
            observed_at = _parse_snapshot_timestamp(candidate.provenance.snapshot_timestamp)
            _assert_candidate_market_data(candidate)
            if evidence_symbol and candidate.provenance.underlying_symbol != evidence_symbol:
                raise ValueError(
                    "candidate underlying does not match validated evidence "
                    f"({candidate.provenance.underlying_symbol} != {evidence_symbol})"
                )
            if candidate.provenance.snapshot_timestamp != observed_at:
                # ``_parse_snapshot_timestamp`` normalizes offsets; this keeps the
                # accepted value timezone-aware without mutating the candidate.
                raise ValueError("candidate snapshot timestamp must be UTC-normalized")

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.CANDIDATES,
                "candidates": candidates,
                "candidate_readiness": CandidateReadiness.READY,
                "candidate_reason": "",
                "risk_proposal": None,
                "selected_candidate_id": None,
                "candidate_selection_error": "",
            },
        )
    except Exception as exc:
        return _fail_state(state, "candidates", f"CandidateBuilder error: {exc}")


# ---------------------------------------------------------------------------
# 5. memory
# ---------------------------------------------------------------------------

def node_memory(
    state: CycleState,
    *,
    semantic_memory,  # SemanticMemory
    episodic_context: dict | None = None,
) -> CycleState:
    """
    Memory: retrieve working + semantic memory context for this cycle.

    PRD §6: layered memory — working (per-cycle), episodic (immutable log),
    semantic (generalized rules from Reflexion).

    NOTE: Embedding provider is unresolved (PRD §5.4). This node uses the
    SemanticMemory boundary, which returns empty until wired.
    """
    if state.is_failure():
        return state

    try:
        semantic_rules = semantic_memory.retrieve(state, limit=5)
        memory_context = {
            "semantic_rules": semantic_rules,
            "episodic": episodic_context or {},
            "cycle_id": state.cycle_id,
            "regime": (
                state.evidence_envelope.regime_signal()
                if state.evidence_envelope
                else "unknown"
            ),
        }
        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.MEMORY,
                "memory_context": memory_context,
            }
        )
    except Exception as exc:
        # Memory failures are non-fatal — continue without memory context
        logger.warning("Memory retrieval failed: %s — continuing without memory", exc)
        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.MEMORY,
                "memory_context": {"error": str(exc), "semantic_rules": []},
            }
        )


# ---------------------------------------------------------------------------
# 6. volatility (LLM)
# ---------------------------------------------------------------------------

def node_volatility(state: CycleState) -> CycleState:
    """
    Volatility Analyst: classify the volatility regime.

    Policy: fast_analysis.
    Model: Claude Haiku 4.5 equivalent (via BluePack).
    """
    if state.is_failure():
        return state

    try:
        regime = (
            state.evidence_envelope.regime_signal()
            if state.evidence_envelope
            else "unknown"
        )
        iv_rank = (
            state.quant_metrics.iv_rank
            if state.quant_metrics
            else 0.0
        )
        iv_pct = (
            state.quant_metrics.iv_percentile
            if state.quant_metrics
            else 0.0
        )

        hv30 = (
            state.quant_metrics.hv30
            if state.quant_metrics
            else None
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Volatility Analyst for an options trading agent. "
                    "Classify the implied volatility regime based on the metrics provided."
                )
            },
            {
                "role": "user",
                "content": (
                    f"IV Rank: {iv_rank:.1f}, IV Percentile: {iv_pct:.1f}, "
                    f"Signal: {regime}. "
                    f"HV30: {f'{hv30 * 100:.1f}%' if hv30 is not None else 'N/A'}. "
                    "Classify as high_iv (IV Rank >= 60 AND IV Percentile >= 60), "
                    "low_iv (<= 30), or neutral. "
                    "Return a VolatilityReport with regime, confidence (0-1), rationale, "
                    "and iv_regime_signal."
                )
            }
        ]

        report, provider = _llm_call(
            node_name="volatility_analyst",
            policy="fast_analysis",
            messages=messages,
            response_model=VolatilityReport,
            correlation_id=f"vol-{state.cycle_id}",
        )

        provider_used = dict(state.provider_used)
        provider_used["volatility"] = provider

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.VOLATILITY,
                "volatility_report": report,
                "provider_used": provider_used,
            }
        )
    except Exception as exc:
        return _fail_state(state, "volatility", f"Volatility Analyst error: {exc}")


# ---------------------------------------------------------------------------
# 7. macro (LLM)
# ---------------------------------------------------------------------------

def node_macro(state: CycleState) -> CycleState:
    """
    Macro/News Analyst: read qualitative context, check for circuit breakers.

    Policy: fast_analysis.
    """
    if state.is_failure():
        return state

    try:
        evidence = state.evidence or {}
        news_items = evidence.get("news", [])[:5]  # Last 5 news items

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Macro/News Analyst. "
                    "Assess market sentiment and flag any upcoming macro events "
                    "that could invalidate a short-premium trade. "
                    "Circuit breaker: NFP, FOMC, CPI, earnings within 24h = halt signal."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Recent news items: {news_items or 'No recent news.'}. "
                    f"Market regime: {state.evidence_envelope.regime_signal() if state.evidence_envelope else 'unknown'}. "
                    "Return a MacroReport with sentiment, confidence, key_events, "
                    "and circuit_breaker_active (True if major event in next 24h)."
                )
            }
        ]

        report, provider = _llm_call(
            node_name="macro_analyst",
            policy="fast_analysis",
            messages=messages,
            response_model=MacroReport,
            correlation_id=f"macro-{state.cycle_id}",
        )

        provider_used = dict(state.provider_used)
        provider_used["macro"] = provider

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.MACRO,
                "macro_report": report,
                "provider_used": provider_used,
            }
        )
    except Exception as exc:
        return _fail_state(state, "macro", f"Macro Analyst error: {exc}")


# ---------------------------------------------------------------------------
# 8. technical (LLM)
# ---------------------------------------------------------------------------

def node_technical(state: CycleState) -> CycleState:
    """
    Technical Manager: synthesize Volatility + Macro into directional conviction.

    Policy: strong_reasoning (Sonnet-equivalent via BluePack).
    """
    if state.is_failure():
        return state

    try:
        vol = state.volatility_report
        macro = state.macro_report
        quant = state.quant_metrics
        memory = state.memory_context or {}

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Technical Manager. "
                    "Synthesize volatility regime and macro context into a "
                    "directional conviction score (0-1) for the trading session. "
                    "0 = strong bearish, 0.5 = neutral, 1 = strong bullish."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Vol regime: {vol.regime if vol else 'unknown'}, "
                    f"IV confidence: {vol.confidence if vol else 0.0:.2f}. "
                    f"Macro sentiment: {macro.sentiment if macro else 'unknown'}, "
                    f"circuit breaker: {macro.circuit_breaker_active if macro else False}. "
                    f"Momentum Z-Score: {quant.momentum_zscore_20d if quant else 0.0:.2f}. "
                    f"HV/IV ratio: {quant.hv_vs_iv_ratio if quant and quant.hv_vs_iv_ratio else 'N/A'}. "
                    f"Semantic rules: {memory.get('semantic_rules', [])}. "
                    "Return a TechnicalReport with trend, momentum_score (-3 to 3), "
                    "directional_conviction (0-1), and a one-sentence summary."
                )
            }
        ]

        report, provider = _llm_call(
            node_name="technical_manager",
            policy="strong_reasoning",
            messages=messages,
            response_model=TechnicalReport,
            correlation_id=f"tech-{state.cycle_id}",
        )

        provider_used = dict(state.provider_used)
        provider_used["technical"] = provider

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.TECHNICAL,
                "technical_report": report,
                "provider_used": provider_used,
            }
        )
    except Exception as exc:
        return _fail_state(state, "technical", f"Technical Manager error: {exc}")


# ---------------------------------------------------------------------------
# 9. bull (LLM)
# ---------------------------------------------------------------------------

def node_bull(state: CycleState) -> CycleState:
    """
    Bull Researcher: argue the case for proceeding with the trade.

    Policy: strong_reasoning (adversarial pair with Bear).
    """
    if state.is_failure():
        return state

    try:
        vol = state.volatility_report
        macro = state.macro_report
        tech = state.technical_report
        quant = state.quant_metrics
        candidates = state.candidates

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Bull Researcher. "
                    "Argue the strongest case FOR proceeding with a short-premium trade. "
                    "Be specific: cite IV Rank, VRP rationale, spread structure, "
                    "and any favorable technical/macro signals. "
                    "Return a BullBearReport with verdict ('proceed'/'reject'/'modify'), "
                    "score (0-1 conviction), arguments (list of bullish points), "
                    "and risk_flags (any concerns you note)."
                )
            },
            {
                "role": "user",
                "content": (
                    f"IV Rank: {quant.iv_rank if quant else 0.0:.1f}, "
                    f"IV Percentile: {quant.iv_percentile if quant else 0.0:.1f}. "
                    f"Vol regime: {vol.regime if vol else 'unknown'}. "
                    f"Macro: {macro.sentiment if macro else 'unknown'}. "
                    f"Technical trend: {tech.trend if tech else 'unknown'}, "
                    f"directional conviction: {tech.directional_conviction if tech else 0.5:.2f}. "
                    f"Candidates: {candidates}. "
                    "Make the bull case for short-premium (Iron Condor / credit spread)."
                )
            }
        ]

        report, provider = _llm_call(
            node_name="bull_researcher",
            policy="strong_reasoning",
            messages=messages,
            response_model=BullBearReport,
            correlation_id=f"bull-{state.cycle_id}",
        )

        provider_used = dict(state.provider_used)
        provider_used["bull"] = provider

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.BULL,
                "bull_report": report,
                "provider_used": provider_used,
            }
        )
    except Exception as exc:
        return _fail_state(state, "bull", f"Bull Researcher error: {exc}")


# ---------------------------------------------------------------------------
# 10. bear (LLM)
# ---------------------------------------------------------------------------

def node_bear(state: CycleState) -> CycleState:
    """
    Bear Researcher: attack the trade case, flag skew anomalies and event risk.

    Policy: strong_reasoning (adversarial pair with Bull).
    """
    if state.is_failure():
        return state

    try:
        vol = state.volatility_report
        macro = state.macro_report
        tech = state.technical_report
        quant = state.quant_metrics
        candidates = state.candidates

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Bear Researcher. "
                    "Critically challenge the case for a short-premium trade. "
                    "Look for: skew anomalies, upcoming event risk, momentum reversal "
                    "signals, low IV rank traps, and liquidity concerns. "
                    "Be adversarial to Bull — if Bull's case is weak, say so clearly. "
                    "Return a BullBearReport with verdict, score (0-1 conviction FOR the trade), "
                    "arguments (list of bearish/warning points), "
                    "and risk_flags (specific dangers you've identified)."
                )
            },
            {
                "role": "user",
                "content": (
                    f"IV Rank: {quant.iv_rank if quant else 0.0:.1f}, "
                    f"IV Percentile: {quant.iv_percentile if quant else 0.0:.1f}. "
                    f"25-delta skew: {quant.skew_25_delta if quant and quant.skew_25_delta is not None else 'N/A'}. "
                    f"Vol regime: {vol.regime if vol else 'unknown'}. "
                    f"Macro: {macro.sentiment if macro else 'unknown'}, "
                    f"key events: {macro.key_events if macro else []}. "
                    f"Technical: {tech.trend if tech else 'unknown'}, "
                    f"momentum score: {tech.momentum_score if tech else 0.0:.2f}. "
                    f"Candidates: {candidates}. "
                    "Critically challenge the short-premium trade."
                )
            }
        ]

        report, provider = _llm_call(
            node_name="bear_researcher",
            policy="strong_reasoning",
            messages=messages,
            response_model=BullBearReport,
            correlation_id=f"bear-{state.cycle_id}",
        )

        provider_used = dict(state.provider_used)
        provider_used["bear"] = provider

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.BEAR,
                "bear_report": report,
                "provider_used": provider_used,
            }
        )
    except Exception as exc:
        return _fail_state(state, "bear", f"Bear Researcher error: {exc}")


# ---------------------------------------------------------------------------
# 11. chief (LLM)
# ---------------------------------------------------------------------------

_CHIEF_TO_TRADE_SIDE = {
    "iron_condor": "iron_condor",
    "bull_put_spread": "short_put_spread",
    "bear_call_spread": "short_call_spread",
}


def _candidate_summaries(state: CycleState) -> list[dict[str, object]]:
    """Provide LLMs only a selection menu, never editable contract payloads."""
    return [
        {
            "proposal_id": candidate.proposal_id,
            "side": candidate.proposal.spread.side.value,
            "dte": candidate.proposal.spread.dte,
            "underlying": candidate.provenance.underlying_symbol,
            "data_tier": candidate.provenance.data_tier,
            "contract_snapshot_id": candidate.provenance.contract_snapshot_id,
        }
        for candidate in state.candidates
    ]


def _select_candidate(
    state: CycleState,
    report: ChiefReport,
) -> tuple[object | None, str]:
    """Resolve a Chief selection to an immutable CandidateBuilder proposal."""
    if report.side == "skip":
        return None, ""

    candidate = next(
        (item for item in state.candidates if item.proposal_id == report.proposal_id),
        None,
    )
    if candidate is None:
        return None, "Chief selected a proposal_id not supplied by CandidateBuilder"

    expected_side = _CHIEF_TO_TRADE_SIDE[report.side]
    if candidate.proposal.spread.side.value != expected_side:
        return None, (
            "Chief selected a side that does not match the locked candidate "
            f"({report.side} != {candidate.proposal.spread.side.value})"
        )
    return candidate, ""


def _chief_safe_skip(state: CycleState, reason: str) -> CycleState:
    """Record a deterministic no-trade outcome without an LLM fallback."""
    report = ChiefReport(
        proposal_id=f"not-ready-{state.cycle_id}",
        side="skip",
        rationale=reason,
        confidence=0.0,
    )
    return state.model_copy(
        deep=True,
        update={
            "phase": CyclePhase.CHIEF,
            "chief_report": report,
            "selected_candidate_id": None,
            "candidate_selection_error": reason,
            "risk_proposal": None,
        },
    )


# ---------------------------------------------------------------------------
# 11. chief (LLM)
# ---------------------------------------------------------------------------

def node_chief(state: CycleState) -> CycleState:
    """Arbitrate qualitative research and select an existing candidate only.

    The Chief cannot create legs, prices, strikes, expiry, sizing, account
    context, or an execution intent.  A non-skip selection is resolved solely
    from ``CandidateBuilder`` output by ``_select_candidate``.
    """
    if state.is_failure():
        return state

    from orchestrator.state import CandidateReadiness

    if state.candidate_readiness != CandidateReadiness.READY:
        return _chief_safe_skip(state, state.candidate_reason or "Candidate path is not ready")
    if state.macro_report and state.macro_report.circuit_breaker_active:
        return _chief_safe_skip(
            state,
            state.macro_report.circuit_breaker_reason or "Macro circuit breaker is active",
        )

    is_repair = bool(state.validator_report and not state.validator_report.valid)
    if is_repair and state.at_max_repair_hops:
        return _fail_state(
            state,
            "chief",
            f"Validator repair limit ({MAX_REPAIR_HOPS}) exhausted",
        )

    try:
        vol = state.volatility_report
        macro = state.macro_report
        tech = state.technical_report
        bull = state.bull_report
        bear = state.bear_report
        quant = state.quant_metrics
        repair_feedback = (
            state.validator_report.errors if is_repair and state.validator_report else []
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Chief Strategy Agent. Arbitrate Bull vs. Bear and "
                    "select exactly one supplied candidate proposal_id, or side='skip'. "
                    "You may not create or change option contracts, strikes, prices, "
                    "expiry, DTE, sizing, account figures, or risk values. "
                    "Return ChiefReport(proposal_id, side, rationale, confidence)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Candidate selection menu: {_candidate_summaries(state)}\n"
                    f"Volatility: {vol.to_dict() if vol else {}}\n"
                    f"Macro: {macro.to_dict() if macro else {}}\n"
                    f"Technical: {tech.to_dict() if tech else {}}\n"
                    f"Bull: {bull.to_dict() if bull else {}}\n"
                    f"Bear: {bear.to_dict() if bear else {}}\n"
                    f"IV Rank: {quant.iv_rank if quant else 'N/A'}\n"
                    f"Validator repair feedback: {repair_feedback}"
                ),
            },
        ]

        report, provider = _llm_call(
            node_name="chief_strategy_agent",
            policy="strong_reasoning",
            messages=messages,
            response_model=ChiefReport,
            correlation_id=f"chief-{state.cycle_id}",
        )
        selected, selection_error = _select_candidate(state, report)
        provider_used = dict(state.provider_used)
        provider_used["chief"] = provider
        repair_hop = state.repair_hop + 1 if is_repair else state.repair_hop

        return state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.CHIEF,
                "chief_report": report,
                "selected_candidate_id": selected.proposal_id if selected else None,
                "candidate_selection_error": selection_error,
                "risk_proposal": selected.proposal.model_copy(deep=True) if selected else None,
                "repair_hop": repair_hop,
                "provider_used": provider_used,
            },
        )
    except Exception as exc:
        return _fail_state(state, "chief", f"Chief Strategy Agent error: {exc}")


# ---------------------------------------------------------------------------
# 12. validator
# ---------------------------------------------------------------------------

def node_validator(state: CycleState) -> CycleState:
    """Validate the Chief's candidate selection, never its invented trade data."""
    if state.is_failure():
        return state

    try:
        from orchestrator.state import CandidateReadiness

        errors: list[str] = []
        warnings: list[str] = []
        report = state.chief_report

        if report is None:
            errors.append("Chief report is missing")
        elif report.side not in (*_CHIEF_TO_TRADE_SIDE, "skip"):
            errors.append(f"Invalid side: {report.side}")
        elif report.side != "skip":
            if state.candidate_readiness != CandidateReadiness.READY:
                errors.append("No validated candidate is ready for a non-skip proposal")
            if state.candidate_selection_error:
                errors.append(state.candidate_selection_error)
            if state.risk_proposal is None:
                errors.append("Selected candidate did not resolve to a TradeProposal")
            elif state.selected_candidate_id != state.risk_proposal.proposal_id:
                errors.append("Selected candidate ID does not match RiskManager proposal")
            else:
                expected_side = _CHIEF_TO_TRADE_SIDE[report.side]
                if state.risk_proposal.spread.side.value != expected_side:
                    errors.append("Selected candidate side does not match Chief selection")
                if not 1 <= state.risk_proposal.spread.dte <= 5:
                    errors.append(
                        f"Candidate DTE {state.risk_proposal.spread.dte} out of allowed range [1, 5]"
                    )
            if report.confidence < 0.3:
                warnings.append(f"Low confidence ({report.confidence:.2f}) — trade rationale is weak")

        valid = not errors
        validator_report = ValidatorReport(
            valid=valid,
            errors=errors,
            warnings=warnings,
            suggested_fix=(
                "Select a supplied CandidateBuilder proposal that matches the requested side"
                if errors
                else ""
            ),
        )
        update = {
            "phase": CyclePhase.VALIDATOR,
            "validator_report": validator_report,
        }
        if errors and state.at_max_repair_hops:
            update.update({
                "phase": CyclePhase.FAILED,
                "failed_node": "validator",
                "error": f"Validator rejected after {MAX_REPAIR_HOPS} repair hops: {'; '.join(errors)}",
            })
        return state.model_copy(deep=True, update=update)
    except Exception as exc:
        return _fail_state(state, "validator", f"Validator error: {exc}")


# ---------------------------------------------------------------------------
# 13. risk_gate
# ---------------------------------------------------------------------------

def _safe_rejection(state: CycleState, proposal_id: str, reason: str, rule_name: str) -> CycleState:
    """Make an explicit non-executable decision without invoking RiskManager."""
    from agents.risk_manager import RiskDecision, RiskStatus

    return state.model_copy(
        deep=True,
        update={
            "phase": CyclePhase.RISK_GATE,
            "risk_decision": RiskDecision(
                proposal_id=proposal_id,
                decision=RiskStatus.REJECTED,
                reasons=[reason],
                rule_names=[rule_name],
            ),
        },
    )


def node_risk_gate(
    state: CycleState,
    *,
    risk_manager,  # RiskManager — the existing deterministic gate
) -> CycleState:
    """Evaluate only a locked CandidateBuilder ``TradeProposal``.

    There is deliberately no dict-to-``TradeProposal`` conversion here.  Missing
    candidate data produces an explicit rejection, and fabricated Chief values
    never reach ``RiskManager.evaluate`` or the execution boundary.
    """
    if state.is_failure():
        return state

    try:
        from agents.risk_manager import RiskStatus
        from orchestrator.state import CandidateReadiness

        report = state.chief_report
        proposal_id = report.proposal_id if report else f"no-chief-{state.cycle_id}"
        if state.candidate_readiness != CandidateReadiness.READY:
            return _safe_rejection(
                state,
                proposal_id,
                state.candidate_reason or "Validated candidate data is not ready",
                "CANDIDATE_NOT_READY",
            )
        if report is None:
            return _safe_rejection(state, proposal_id, "Chief report is missing", "CHIEF_REPORT_MISSING")
        if report.side == "skip":
            return _safe_rejection(
                state, proposal_id, "Chief recommended skip — no trade this cycle", "CHIEF_SKIP"
            )
        if state.validator_report is None or not state.validator_report.valid:
            return _safe_rejection(
                state, proposal_id, "Validator did not approve the candidate selection", "VALIDATOR_REJECTED"
            )
        if state.risk_proposal is None or state.selected_candidate_id is None:
            return _safe_rejection(
                state, proposal_id, "Validated candidate selection is missing", "CANDIDATE_SELECTION_INVALID"
            )

        selected = next(
            (item for item in state.candidates if item.proposal_id == state.selected_candidate_id),
            None,
        )
        if selected is None or selected.proposal != state.risk_proposal:
            return _safe_rejection(
                state, proposal_id, "Risk proposal is not the locked candidate payload", "CANDIDATE_PROVENANCE_MISMATCH"
            )

        decision = risk_manager.evaluate(state.risk_proposal)
        if decision.decision == RiskStatus.APPROVED and decision.order_intent is not None:
            decision = decision.model_copy(
                deep=True,
                update={
                    "order_intent": decision.order_intent.model_copy(
                        deep=True,
                        update={"account_id": state.alpaca_account_id},
                    )
                },
            )
        return state.model_copy(
            deep=True,
            update={"phase": CyclePhase.RISK_GATE, "risk_decision": decision},
        )
    except Exception as exc:
        return _fail_state(state, "risk_gate", f"RiskGate error: {exc}")


# ---------------------------------------------------------------------------
# 14. persist
# ---------------------------------------------------------------------------

def node_persist(
    state: CycleState,
    *,
    persistence,  # CyclePersistence
) -> CycleState:
    """Persist the final, account-scoped cycle outcome without execution side effects."""
    try:
        final_state = state.model_copy(
            deep=True,
            update={
                "phase": CyclePhase.FAILED if state.is_failure() else CyclePhase.COMPLETE,
                "completed_at": datetime.now(timezone.utc),
                "persisted": True,
                "persist_error": "",
            },
        )
        success, err = persistence.save(final_state)
        if not success:
            return _fail_state(
                state,
                "persist",
                f"Persistence error: {err or 'storage boundary returned failure'}",
            ).model_copy(
                deep=True,
                update={"persisted": False, "persist_error": err},
            )
        return final_state
    except Exception as exc:
        return _fail_state(
            state,
            "persist",
            f"Persistence error: {exc}",
        ).model_copy(
            deep=True,
            update={"persisted": False, "persist_error": str(exc)},
        )
