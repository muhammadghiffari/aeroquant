"""Skew and probability-of-profit proxies."""


def skew_metrics(chain_summary: dict) -> dict:
    p = chain_summary.get("put_25delta") or {}
    c = chain_summary.get("call_25delta") or {}
    out = {}
    if p.get("iv") and c.get("iv"):
        out["skew_put_call_25delta"] = round(p["iv"] - c["iv"], 4)
        out["skew_note"] = "positive => richer downside protection demand"
    return out


def pop_proxy(strategy_type: str, short_leg_delta: float | None = None,
              long_leg_delta: float | None = None) -> float | None:
    """Crude probability-of-profit proxies communicated to agents as estimates.

    credit spread ~ 1 - |delta_short|; long option ~ |delta|; iron condor
    approximated by the nearer (higher-delta) short leg.
    """
    if strategy_type in ("CREDIT_SPREAD", "IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
        if short_leg_delta is None:
            return None
        d = min(abs(short_leg_delta), 0.95)
        return round(1 - d, 3)
    if strategy_type in ("LONG_CALL", "LONG_PUT", "STRADDLE", "STRANGLE", "DEBIT_SPREAD"):
        deltas = [abs(d) for d in (long_leg_delta,) if d is not None]
        if not deltas:
            return None
        return round(min(deltas[0], 0.95), 3)
    return None
