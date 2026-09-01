"""
data_engine/options_pricing.py — Deterministic options pricing utilities.

Provides:
  - Black-Scholes delta calculation (for strike selection)
  - Delta-to-strike inversion for analytical scenario work
  - Black-Scholes option price estimation for analytical scenario work

All functions are pure and deterministic — no network, no randomness.
They are analytical utilities only: executable candidates must select supplied
option-chain contracts and quotes, never Black-Scholes-derived contracts or prices.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone


def _norm_cdf(x: float) -> float:
    """Standard normal CDF, evaluated with the platform error function."""
    if math.isnan(x):
        return math.nan
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF using the Acklam rational approximation."""
    if math.isnan(p):
        return math.nan
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    if p == 0.5:
        return 0.0

    # Rational approximation due to Peter J. Acklam
    a = [
        -3.969683028665376e1,
        2.209460984245205e2,
        -2.759285104469687e2,
        1.383577518672690e2,
        -3.066479806614716e1,
        2.506628277459239e0,
    ]
    b = [
        -5.447609879822406e1,
        1.615858368580409e2,
        -1.556989798598866e2,
        6.680131188771972e1,
        -1.328068155288572e1,
    ]
    c = [
        -7.784894002430293e-3,
        -3.223964580411365e-1,
        -2.400758277161838e0,
        -2.549732539343734e0,
        4.374664141464968e0,
        2.938163982698783e0,
    ]
    d = [
        7.784695709041462e-3,
        3.224671290700398e-1,
        2.445134137142996e0,
        3.754408661907416e0,
    ]

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        numerator = (
            (((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]
        ) * q + c[5]
        denominator = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        return numerator / denominator
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            ((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]
        ) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        numerator = (
            (((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]
        ) * q + c[5]
        denominator = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        return -numerator / denominator


# ---------------------------------------------------------------------------
# Black-Scholes delta
# ---------------------------------------------------------------------------

def bs_put_delta(
    spot: float, strike: float, iv: float, dte: int, r: float = 0.05, q: float = 0.0
) -> float:
    """
    Black-Scholes delta for a put option.

    delta_put = N(d1) - 1  (where N is standard normal CDF)
    Put delta is negative: it approaches 0 for far OTM puts (K << S)
    and -1 for deep ITM puts (K >> S).

    Args:
        spot: current underlying price
        strike: strike price
        iv: implied volatility (annualized decimal, e.g. 0.18)
        dte: calendar days to expiration
        r: risk-free rate (annualized, default 5%)

    Returns:
        Put delta in [-1, 0] for valid inputs.
    """
    if dte <= 0:
        return 0.0
    if spot <= 0 or strike <= 0 or iv <= 0 or not all(
        math.isfinite(value) for value in (spot, strike, iv, r, q)
    ):
        return 0.0

    T = dte / 365.0
    sqrt_T = math.sqrt(T)
    vol_sqrt_T = iv * sqrt_T

    if vol_sqrt_T < 1e-10:
        return -math.exp(-q * T) if strike > spot else 0.0

    d1 = (math.log(spot / strike) + (r - q + iv * iv / 2.0) * T) / vol_sqrt_T

    # Put delta = N(d1) - 1 (N is standard normal CDF)
    return math.exp(-q * T) * (_norm_cdf(d1) - 1.0)


def bs_call_delta(
    spot: float, strike: float, iv: float, dte: int, r: float = 0.05, q: float = 0.0
) -> float:
    """
    Black-Scholes delta for a call option.

    delta_call = N(d1)  (where N is standard normal CDF)
    For OTM calls (K > S): delta approaches 0; for ITM calls, approaches 1.
    """
    if dte <= 0:
        return 0.0
    if spot <= 0 or strike <= 0 or iv <= 0 or not all(
        math.isfinite(value) for value in (spot, strike, iv, r, q)
    ):
        return 0.0

    T = dte / 365.0
    sqrt_T = math.sqrt(T)
    vol_sqrt_T = iv * sqrt_T

    if vol_sqrt_T < 1e-10:
        return math.exp(-q * T) if strike <= spot else 0.0

    d1 = (math.log(spot / strike) + (r - q + iv * iv / 2.0) * T) / vol_sqrt_T
    return math.exp(-q * T) * _norm_cdf(d1)


# ---------------------------------------------------------------------------
# Delta-to-strike approximation
# ---------------------------------------------------------------------------

def put_strike_from_delta(
    spot: float, iv: float, dte: int, target_delta: float = -0.16, r: float = 0.05, q: float = 0.0
) -> float:
    """
    Estimate the strike that gives approximately the target put delta.

    Uses the Black-Scholes delta inversion: delta_put ≈ N(d1) - 1.
    Solving for K: K ≈ S * exp(-z * σ * √T) where z = N⁻¹(target_delta + 1).

    For 16-delta put: target_delta = -0.16 → z ≈ 0.994
    For 25-delta put: target_delta = -0.25 → z ≈ 0.674

    Args:
        spot: current underlying price
        iv: implied volatility (annualized)
        dte: calendar days to expiration
        target_delta: desired put delta (negative, e.g. -0.16)
        r: risk-free rate

    Returns:
        Strike price that approximately achieves the target delta.
    """
    if dte <= 0:
        return spot
    if iv <= 0:
        return spot
    if spot <= 0 or not all(math.isfinite(value) for value in (spot, iv, r, q)):
        return 0.0

    T = dte / 365.0
    p = 1.0 + target_delta * math.exp(q * T)
    if not 0.0 < p < 1.0:
        return spot
    z = _norm_ppf(p)

    sqrt_T = math.sqrt(T)

    # Approximation: K ≈ S * exp(-z * σ * √T)
    # z is positive for put deltas in (-1, 0)
    # For 16-delta: z ≈ 0.994; strike is below spot
    strike = spot * math.exp((r - q + iv * iv / 2.0) * T - z * iv * sqrt_T)

    # Round to nearest $0.50 (XSP uses $0.50 strike increments)
    strike = round(strike * 2.0) / 2.0
    return max(0.5, strike)


def call_strike_from_delta(
    spot: float, iv: float, dte: int, target_delta: float = 0.16, r: float = 0.05, q: float = 0.0
) -> float:
    """
    Estimate the strike that gives approximately the target call delta.

    Uses the Black-Scholes delta: delta_call = N(d1).
    Solving for K: K ≈ S * exp(-z * σ * √T) where z = N⁻¹(target_delta).

    For 16-delta call: target_delta = 0.16 → z ≈ -0.994
    For 25-delta call: target_delta = 0.25 → z ≈ -0.674

    Args:
        spot: current underlying price
        iv: implied volatility (annualized)
        dte: calendar days to expiration
        target_delta: desired call delta (positive, e.g. 0.16)
        r: risk-free rate

    Returns:
        Strike price that approximately achieves the target delta.
    """
    if dte <= 0:
        return spot
    if iv <= 0:
        return spot
    if spot <= 0 or not all(math.isfinite(value) for value in (spot, iv, r, q)):
        return 0.0

    T = dte / 365.0
    p = target_delta * math.exp(q * T)
    if not 0.0 < p < 1.0:
        return spot
    z = _norm_ppf(p)
    sqrt_T = math.sqrt(T)

    # Approximation: K ≈ S * exp(-z * σ * √T)
    # z is negative for call deltas in (0, 1)
    # For 16-delta: z ≈ -0.994; strike is above spot
    strike = spot * math.exp((r - q + iv * iv / 2.0) * T - z * iv * sqrt_T)

    # Round to nearest $0.50 (XSP uses $0.50 strike increments)
    strike = round(strike * 2.0) / 2.0
    return max(0.5, strike)


# ---------------------------------------------------------------------------
# Black-Scholes price estimation (analytical utilities only)
# ---------------------------------------------------------------------------

def bs_put_price(spot: float, strike: float, iv: float, dte: int, r: float = 0.05, q: float = 0.0) -> float:
    """Black-Scholes put price."""
    if dte <= 0:
        return max(0.0, strike - spot)
    if spot <= 0 or strike <= 0 or iv <= 0 or not all(
        math.isfinite(value) for value in (spot, strike, iv, r, q)
    ):
        return 0.0

    T = dte / 365.0
    sqrt_T = math.sqrt(T)
    vol_sqrt_T = iv * sqrt_T

    if vol_sqrt_T < 1e-10:
        return max(0.0, strike * math.exp(-r * T) - spot * math.exp(-q * T))

    d1 = (math.log(spot / strike) + (r - q + iv * iv / 2.0) * T) / vol_sqrt_T
    d2 = d1 - vol_sqrt_T

    put = strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * math.exp(-q * T) * _norm_cdf(-d1)
    return max(0.0, put)


def bs_call_price(spot: float, strike: float, iv: float, dte: int, r: float = 0.05, q: float = 0.0) -> float:
    """Black-Scholes call price."""
    if dte <= 0:
        return max(0.0, spot - strike)
    if spot <= 0 or strike <= 0 or iv <= 0 or not all(
        math.isfinite(value) for value in (spot, strike, iv, r, q)
    ):
        return 0.0

    T = dte / 365.0
    sqrt_T = math.sqrt(T)
    vol_sqrt_T = iv * sqrt_T

    if vol_sqrt_T < 1e-10:
        return max(0.0, spot * math.exp(-q * T) - strike * math.exp(-r * T))

    d1 = (math.log(spot / strike) + (r - q + iv * iv / 2.0) * T) / vol_sqrt_T
    d2 = d1 - vol_sqrt_T

    call = spot * math.exp(-q * T) * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2)
    return max(0.0, call)


# ---------------------------------------------------------------------------
# Strike selection constants
# ---------------------------------------------------------------------------

# XSP uses $0.50 strike increments
XSP_STRIKE_INCREMENT = 0.50

# Short strike target delta (per PRD Iron Condor rules)
SHORT_PUT_DELTA_TARGET = -0.16   # ~16 delta put
SHORT_CALL_DELTA_TARGET = 0.16   # ~16 delta call

# DTE range for Iron Condor entries (PRD §8)
MIN_DTE = 1
MAX_DTE = 5
PREFERRED_DTE = 3   # prefer mid-range DTE


def nearest_valid_expiry(
    available_expiries: list[str],
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
    preferred_dte: int = PREFERRED_DTE,
    as_of: date | datetime | None = None,
) -> str | None:
    """
    Select the nearest valid expiry from available expirations.

    Args:
        available_expiries: list of expiry dates as strings (YYYY-MM-DD)
        min_dte, max_dte: allowed DTE range
        preferred_dte: preferred DTE (select closest available to this)
        as_of: UTC reference date/time used for deterministic selection.

    Returns:
        Selected expiry string, or None if no valid expiry found.
    """
    if (
        isinstance(min_dte, bool)
        or isinstance(max_dte, bool)
        or isinstance(preferred_dte, bool)
        or not all(isinstance(value, int) for value in (min_dte, max_dte, preferred_dte))
        or min_dte < 0
        or max_dte < min_dte
    ):
        return None
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            return None
        today = as_of.astimezone(timezone.utc).date()
    elif isinstance(as_of, date):
        today = as_of
    else:
        return None

    def dte_from_expiry(expiry_str: object) -> int | None:
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            return (expiry_date - today).days
        except (TypeError, ValueError):
            return None

    valid = [
        (dte, expiry)
        for expiry in available_expiries
        if (dte := dte_from_expiry(expiry)) is not None and min_dte <= dte <= max_dte
    ]

    if not valid:
        return None

    # Sort by preference: first by distance to preferred DTE, then by DTE
    valid.sort(key=lambda x: (abs(x[0] - preferred_dte), x[0], x[1]))
    return valid[0][1]
