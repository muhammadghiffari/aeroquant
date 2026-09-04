from datetime import datetime, timezone

import numpy as np
import pandas as pd

from data_engine.option_data import OptionContract
from quant_engine import engine
from quant_engine.momentum import (
    build_momentum_signal,
    estimate_target_probability,
    score_reversal,
)


def _bars(start: float, end: float, count: int = 90) -> pd.DataFrame:
    index = pd.date_range(
        datetime(2026, 1, 1, tzinfo=timezone.utc), periods=count, freq="D"
    )
    close = np.linspace(start, end, count)
    return pd.DataFrame({"close": close, "volume": [1000] * count}, index=index)


def test_momentum_signal_requires_empirical_bullish_probability():
    result = build_momentum_signal(_bars(100, 140), min_samples=30, horizon=3)

    assert result["direction"] == "BULLISH"
    assert result["actionable"] is True
    assert result["sample_size"] >= 30
    assert result["probability_lower_bound"] > 0.5


def test_momentum_signal_defaults_to_one_forward_hour():
    result = build_momentum_signal(_bars(100, 140))

    assert result["horizon"] == 1


def test_momentum_signal_mirrors_bearish_direction():
    result = build_momentum_signal(_bars(140, 100), min_samples=30, horizon=3)

    assert result["direction"] == "BEARISH"
    assert result["actionable"] is True


def test_momentum_signal_fails_closed_when_sample_is_insufficient():
    result = build_momentum_signal(_bars(100, 110, count=20), min_samples=30)

    assert result["direction"] == "WAIT"
    assert result["actionable"] is False
    assert "insufficient_samples" in result["reasons"]


def test_momentum_signal_reports_wait_strategy_when_probability_gate_fails():
    closes = np.r_[np.full(69, 100.0), np.linspace(100.0, 101.0, 21)]
    result = build_momentum_signal(
        pd.DataFrame({"close": closes, "volume": [1000] * len(closes)}),
        min_samples=30,
        horizon=5,
    )

    assert result["direction"] == "WAIT"
    assert result["directional_bias"] == "BULLISH"
    assert result["strategy_type"] == "WAIT"
    assert result["actionable"] is False


def test_target_probability_uses_forward_horizon_and_lower_bound():
    result = estimate_target_probability([0.02] * 40, required_move=0.03, horizon=2)

    assert result["sample_size"] == 39
    assert result["successes"] == 39
    assert result["probability"] == 1.0
    assert result["lower_bound"] > 0.9


def test_reversal_requires_two_bars_and_two_indicator_votes():
    history = [
        {"ema_fast": 9.0, "ema_slow": 10.0, "price": 9.5, "vwap": 10.0, "momentum": -1.0},
        {"ema_fast": 8.0, "ema_slow": 10.0, "price": 9.0, "vwap": 10.0, "momentum": -2.0},
    ]

    result = score_reversal(history, position_direction="LONG_CALL")

    assert result["confirmed"] is True
    assert result["bars_confirmed"] == 2
    assert result["votes"] == 3


def test_quant_report_persists_versioned_momentum_gate(monkeypatch):
    chain = [OptionContract(
        symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
        opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
        spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
        quote_timestamp=datetime.now(timezone.utc).isoformat(),
    )]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    momentum_kwargs = {}
    original_momentum = engine.build_momentum_signal

    def capture_momentum(bars, **kwargs):
        momentum_kwargs.update(kwargs)
        return original_momentum(bars, **kwargs)

    monkeypatch.setattr(engine, "build_momentum_signal", capture_momentum)

    report = engine.build_quant_report("SPY")

    assert report["momentum"]["quant_version"] == "deterministic-single-leg-momentum-v1"
    assert report["momentum"]["direction"] == "BULLISH"
    assert report["momentum"]["sample_size"] >= 30
    assert report["momentum"]["underlying_confidence"]["confidence_version"] == "setup-conditioned-momentum-v1"
    assert report["momentum"]["confidence"]["confidence_version"] == "underlying-history-1h-v1"
    assert report["momentum"]["audit"]["unconditional_lower_bound"] == report["momentum"]["probability_lower_bound"]
    assert momentum_kwargs["horizon"] == 1


def test_quant_report_attaches_only_directional_option_candidates(monkeypatch):
    chain = [
        OptionContract(
            symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        OptionContract(
            symbol="SPY260919P00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="put", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=-0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    ]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(engine.shadow_store, "resolved_outcomes", lambda: [{"profitable": True}] * 30)

    report = engine.build_quant_report("SPY")

    candidates = report["momentum"]["candidates"]
    assert candidates
    assert all(item["strategy_type"] == "LONG_CALL" for item in candidates)
    assert all(item["direction"] == "BULLISH" for item in candidates)
    assert all(item["confidence_source"] == "contract_history" for item in candidates)
    assert all(
        item["probability_lower_bound"] == item["contract_confidence"]["lower_bound"]
        for item in candidates
    )


def test_quant_report_confidence_wait_blocks_candidate_whitelist(monkeypatch):
    chain = [OptionContract(
        symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
        opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
        spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
        quote_timestamp=datetime.now(timezone.utc).isoformat(),
    )]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(engine, "build_confidence_signal", lambda *_args, **_kwargs: {
        "confidence_version": "setup-conditioned-momentum-v1",
        "state": "WAIT_SEE",
        "direction": "BULLISH",
        "setup_lower_bound": 0.54,
        "actionable": False,
        "reasons": ["setup_lower_bound_below_threshold"],
    })

    report = engine.build_quant_report("SPY")

    assert report["momentum"]["underlying_confidence"]["state"] == "WAIT_SEE"
    assert report["momentum"]["confidence"]["state"] == "WAIT_SEE"
    assert report["momentum"]["contract_confidence"]["state"] == "WAIT_DATA"
    assert report["momentum"]["candidates"] == []
    assert report["momentum"]["entry_actionable"] is False


def test_quant_report_allows_proxy_without_calibrated_contract_outcomes(monkeypatch):
    chain = [
        OptionContract(
            symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        OptionContract(
            symbol="SPY260919P00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="put", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=-0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    ]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(engine, "shadow_store", type("Store", (), {
        "resolved_outcomes": staticmethod(lambda: []),
    })())
    monkeypatch.setattr(engine, "build_confidence_signal", lambda *_args, **_kwargs: {
        "state": "WAIT_SEE", "direction": "BULLISH", "setup_probability": 0.65,
        "setup_lower_bound": 0.52, "volatility_regime": "LOW",
        "horizon_alignment": {"passed": True, "agreeing": 3, "total": 3},
    })

    report = engine.build_quant_report("SPY")

    assert report["momentum"]["confidence"]["state"] == "WAIT_SEE"
    assert report["momentum"]["contract_confidence"]["state"] == "WAIT_DATA"
    assert report["momentum"]["entry_actionable"] is True
    assert report["momentum"]["entry_mode"] == "UNDERLYING_HISTORY_PROXY"
    assert report["momentum"]["shadow_candidates"]


def test_quant_report_uses_underlying_history_proxy_before_option_outcomes(monkeypatch):
    chain = [
        OptionContract(
            symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        OptionContract(
            symbol="SPY260919P00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="put", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=-0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    ]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(engine, "shadow_store", type("Store", (), {
        "resolved_outcomes": staticmethod(lambda **_kwargs: []),
        "pending_observations": staticmethod(lambda *_args, **_kwargs: []),
    })())
    monkeypatch.setattr(engine, "build_confidence_signal", lambda *_args, **_kwargs: {
        "confidence_version": "setup-conditioned-momentum-v1",
        "state": "ENTER_CONFIRMED",
        "direction": "BULLISH",
        "setup_probability": 0.70,
        "setup_lower_bound": 0.64,
        "setup_successes": 80,
        "setup_sample_size": 120,
        "volatility_regime": "LOW",
        "horizon_alignment": {"passed": True, "agreeing": 3, "total": 3},
        "actionable": True,
        "horizon": 1,
    })

    report = engine.build_quant_report("SPY")
    momentum = report["momentum"]

    assert momentum["entry_confidence"]["state"] == "GREEN_PROXY"
    assert momentum["entry_confidence"]["historical_confidence_advisory"] is True
    assert momentum["entry_confidence"]["source"] == "stock_bars"
    assert momentum["contract_confidence"]["state"] == "WAIT_DATA"
    assert momentum["entry_actionable"] is True
    assert momentum["candidates"]


def test_quant_report_proxy_uses_historical_probability_not_contract_calibration(monkeypatch):
    chain = [OptionContract(
        symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
        opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
        spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
        quote_timestamp=datetime.now(timezone.utc).isoformat(),
        bid_size=30, ask_size=80,
    ), OptionContract(
        symbol="SPY260919P00500000", underlying="SPY", expiry="2026-09-19",
        opt_type="put", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
        spread_pct=0.0488, iv=0.2, delta=-0.55, dte=18,
        quote_timestamp=datetime.now(timezone.utc).isoformat(),
    )]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(engine.shadow_store, "resolved_outcomes", lambda **_kwargs: [])
    monkeypatch.setattr(engine.shadow_store, "pending_observations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "build_momentum_signal", lambda *_args, **_kwargs: {
        "quant_version": "deterministic-single-leg-momentum-v1",
        "direction": "WAIT",
        "directional_bias": "BULLISH",
        "strategy_type": "WAIT",
        "actionable": False,
        "probability": 0.4756,
        "probability_lower_bound": 0.4342,
        "sample_size": 389,
        "expected_value_after_costs": None,
        "horizon": 1,
        "features": {},
        "data_quality": {"bar_count": 390, "valid": True},
        "reasons": ["probability_lower_bound_below_threshold"],
    })
    monkeypatch.setattr(engine, "build_confidence_signal", lambda *_args, **_kwargs: {
        "confidence_version": "setup-conditioned-momentum-v1",
        "state": "WAIT_SEE",
        "direction": "BULLISH",
        "setup_probability": 0.42,
        "setup_lower_bound": 0.42,
        "setup_successes": 44,
        "setup_sample_size": 80,
        "volatility_regime": "LOW",
        "horizon_alignment": {"passed": True, "agreeing": 2, "total": 3},
        "actionable": False,
        "horizon": 1,
    })

    momentum = engine.build_quant_report("SPY")["momentum"]

    assert momentum["entry_mode"] == "UNDERLYING_HISTORY_PROXY"
    assert momentum["entry_actionable"] is True
    assert momentum["direction"] == "BULLISH"
    assert momentum["strategy_type"] == "LONG_CALL"
    assert momentum["actionable"] is True
    assert momentum["entry_confidence"]["state"] == "WAIT_SEE"
    assert momentum["entry_confidence"]["historical_confidence_advisory"] is True
    assert momentum["contract_confidence"]["state"] == "WAIT_DATA"
    assert momentum["candidates"][0]["confidence_source"] == "underlying_history_proxy"
    assert momentum["candidates"][0]["probability"] == 0.42


def test_quant_report_allows_only_green_contract_candidates(monkeypatch):
    chain = [
        OptionContract(
            symbol="SPY260919C00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="call", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        OptionContract(
            symbol="SPY260919P00500000", underlying="SPY", expiry="2026-09-19",
            opt_type="put", strike=500.0, bid=2.0, ask=2.1, mid=2.05,
            spread_pct=0.0488, iv=0.2, delta=-0.55, dte=18,
            quote_timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    ]
    monkeypatch.setattr(engine, "get_daily_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "get_hourly_bars", lambda *_args, **_kwargs: _bars(100, 140, count=140))
    monkeypatch.setattr(engine, "fetch_chain", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(engine, "shadow_store", type("Store", (), {
        "resolved_outcomes": staticmethod(lambda: [{"profitable": True}] * 30),
    })())
    monkeypatch.setattr(engine, "build_confidence_signal", lambda *_args, **_kwargs: {
        "state": "WAIT_SEE", "direction": "BULLISH", "setup_probability": 0.70,
        "setup_lower_bound": 0.52, "volatility_regime": "LOW",
        "horizon_alignment": {"passed": True, "agreeing": 3, "total": 3},
    })

    report = engine.build_quant_report("SPY")

    assert report["momentum"]["contract_confidence"]["state"] == "GREEN"
    assert report["momentum"]["confidence"]["state"] == "GREEN"
    assert report["momentum"]["entry_actionable"] is True
    assert all(item["strategy_type"] == "LONG_CALL" for item in report["momentum"]["candidates"])
