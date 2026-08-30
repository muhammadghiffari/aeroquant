"""
AeroQuant -- Empirical win-rate / breakeven validator for the Iron Condor selection rule.

PURPOSE
-------
The PRD's SL/TP calibration in §8 (50% TP / 125% SL, revised 2.4) implies a specific
breakeven win rate. This script does NOT assume a win rate -- it computes one from
YOUR actual historical strike-selection logic (§5.1's Expected-Move-based placement)
against real underlying + IV history, so the number going into the pitch deck is
measured, not guessed (unlike the Monte Carlo illustration used in chat, which used
an assumed 62-75% range).

INPUT YOU NEED TO SUPPLY (see load_data() below):
  - Daily (or intraday) underlying close series for the trading window you want to
    backtest against (Tier 1: SPY as XSP proxy per §12; Tier 2: Tradier SPX chain
    if available; Tier 3: yfinance ^GSPC for pre-kickoff historical depth).
  - Daily ATM IV or HV30 proxy, to replicate the EM% calculation from §5.1.

This does NOT hit any network -- it's a pure function of whatever DataFrame you load.
Wire load_data() to your actual Day-0 pipeline (the IV snapshot cron / EOD pull).
"""

import numpy as np
import pandas as pd


def expected_move_pct(iv_annualized: float, dte: int) -> float:
    """EM% approximation: sigma * sqrt(dte/365), matching §5.1's ATM-straddle-cost logic
    closely enough for backtest strike placement (exact ATM straddle price backtest
    requires real option chain history, which most vendors don't retain -- this is
    the standard practitioner approximation used when only IV + spot history exists)."""
    return iv_annualized * np.sqrt(dte / 365.0)


def backtest_win_rate(
    underlying: pd.Series,      # daily close, indexed by date
    iv: pd.Series,               # daily ATM IV (annualized, e.g. 0.18 = 18%), same index
    dte: int,                    # holding period in calendar days to test (e.g. 3 for mid-band)
    short_strike_em_mult: float, # how far OTM to place short strikes, in units of EM%
                                   # e.g. 1.0 = short strike at the edge of the 1-EM% expected range
    width_pct: float = 0.02,     # spread width as % of spot (tune to match XSP tick sizes)
) -> dict:
    """
    For each entry date t, simulate placing a short strike at
    spot(t) * (1 +/- short_strike_em_mult * EM%(t)), held to t+dte, and check whether
    the underlying's actual move at t+dte stayed inside the short strikes (= win,
    condor expires worthless / near max profit) or breached them (= loss).

    Returns win_rate, n_trades, and the breakeven win rate implied by your current
    §8 TP/SL setting, so you can compare them directly.
    """
    dates = underlying.index
    wins, total = 0, 0
    for i, t in enumerate(dates):
        exit_idx = i + dte
        if exit_idx >= len(dates):
            break
        spot_t = underlying.iloc[i]
        spot_exit = underlying.iloc[exit_idx]
        em_pct = expected_move_pct(iv.iloc[i], dte)

        upper_strike = spot_t * (1 + short_strike_em_mult * em_pct)
        lower_strike = spot_t * (1 - short_strike_em_mult * em_pct)

        stayed_inside = lower_strike <= spot_exit <= upper_strike
        wins += int(stayed_inside)
        total += 1

    win_rate = wins / total if total else float("nan")
    return {"win_rate": win_rate, "n_trades": total, "dte": dte, "em_mult": short_strike_em_mult}


def breakeven_win_rate(tp_frac: float, sl_frac: float) -> float:
    return sl_frac / (sl_frac + tp_frac)


def load_data() -> tuple[pd.Series, pd.Series]:
    """
    STUB -- wire this to your actual Day-0 data.
    Example for Tier 1 (SPY as XSP proxy, per PRD §12):

        import pandas as pd
        df = pd.read_csv("your_day0_eod_pull.csv", parse_dates=["date"]).set_index("date")
        underlying = df["spy_close"]
        iv = df["atm_iv"]  # from your 15-30min IV snapshot cron, resampled to daily close
        return underlying, iv

    Raises here on purpose so this script is never silently run on fake data.
    """
    raise NotImplementedError(
        "Wire load_data() to your Day-0 EOD/IV pull before running. "
        "See PRD §5.1/§12 for the SPY-proxy and IV-cron data sources already planned."
    )


if __name__ == "__main__":
    underlying, iv = load_data()

    print(f"{'DTE':>4} | {'EM mult':>8} | {'win_rate':>9} | {'n_trades':>9}")
    print("-" * 40)
    for dte in [1, 2, 3, 5]:
        for em_mult in [0.75, 1.00, 1.25]:
            r = backtest_win_rate(underlying, iv, dte=dte, short_strike_em_mult=em_mult)
            print(f"{r['dte']:>4} | {r['em_mult']:>8.2f} | {r['win_rate']:>8.1%} | {r['n_trades']:>9}")

    print("\nBreakeven win rate needed at current §8 setting (TP=50%, SL=125%):",
          f"{breakeven_win_rate(0.50, 1.25):.1%}")
    print("Compare each row above against this -- any combination below it is net-EV-negative")
    print("at that DTE/strike-placement choice, before even considering execution costs.")
