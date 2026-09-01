"""
data_engine — Deterministic quantitative data layer.

This package is responsible ONLY for data normalization and quantitative metrics.
No LLM calls. No Alpaca trading API calls.
"""

from data_engine.quant_engine import (
    QuantMetrics,
    QuantEngine,
    EvidenceEnvelope,
    MarketDataBundle,
    DataQuality,
    SCHEMA_VERSION,
)

__all__ = [
    "QuantMetrics",
    "QuantEngine",
    "EvidenceEnvelope",
    "MarketDataBundle",
    "DataQuality",
    "SCHEMA_VERSION",
]
