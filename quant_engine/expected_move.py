"""Expected move from ATM straddle (numeric only)."""


def expected_move(spot: float, atm_call, atm_put) -> dict:
    """Expected move % = (call_mid + put_mid) / spot * 100 at the chosen expiry."""
    if not atm_call or not atm_put or not spot:
        return {"expected_move_pct": None}
    straddle = atm_call.mid + atm_put.mid
    pct = straddle / spot * 100.0
    return {
        "atm_straddle_price": round(straddle, 2),
        "atm_call_symbol": atm_call.symbol,
        "atm_put_symbol": atm_put.symbol,
        "expiry_used": atm_call.expiry,
        "dte": atm_call.dte,
        "expected_move_pct": round(pct, 2),
        "expected_move_abs": round(spot * pct / 100.0, 2),
    }
