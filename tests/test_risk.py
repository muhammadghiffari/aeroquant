"""Risk manager structure validation tests (no network / no LLM)."""
from types import SimpleNamespace

from agents.risk_manager_agent import _structure_consistent, _unit_metrics, run_rule_checks


def _contract(strike, opt_type="put", mid=1.0):
    return SimpleNamespace(strike=strike, opt_type=opt_type, mid=mid)


def _leg(action, strike, opt_type="put", mid=1.0):
    return {"action": action, "_contract": _contract(strike, opt_type, mid)}


# ---------------------------------------------------------------- structure
def test_bull_put_correct_direction():
    legs = [_leg("SELL", 762), _leg("BUY", 758)]
    assert _structure_consistent("BULL_PUT_SPREAD", legs)


def test_bull_put_inverted_rejected():
    legs = [_leg("SELL", 758), _leg("BUY", 762)]
    assert not _structure_consistent("BULL_PUT_SPREAD", legs)


def test_bear_call_correct_direction():
    legs = [_leg("SELL", 766, "call"), _leg("BUY", 770, "call")]
    assert _structure_consistent("BEAR_CALL_SPREAD", legs)


def test_bear_call_inverted_rejected():
    legs = [_leg("SELL", 770, "call"), _leg("BUY", 766, "call")]
    assert not _structure_consistent("BEAR_CALL_SPREAD", legs)


def test_debit_spread_put_bullish_direction():
    assert _structure_consistent("DEBIT_SPREAD", [_leg("BUY", 764), _leg("SELL", 760)])
    assert not _structure_consistent("DEBIT_SPREAD", [_leg("BUY", 760), _leg("SELL", 764)])


def test_debit_spread_call_bullish_direction():
    assert _structure_consistent("DEBIT_SPREAD", [_leg("BUY", 760, "call"), _leg("SELL", 764, "call")])


def test_long_put_single_buy():
    assert _structure_consistent("LONG_PUT", [_leg("BUY", 750)])
    assert not _structure_consistent("LONG_PUT", [_leg("SELL", 750)])


def test_iron_condor_wing_order():
    legs = [
        _leg("BUY", 750), _leg("SELL", 755),
        _leg("SELL", 772, "call"), _leg("BUY", 777, "call"),
    ]
    assert _structure_consistent("IRON_CONDOR", legs)


def test_iron_condor_wrong_order_rejected():
    legs = [
        _leg("SELL", 750), _leg("BUY", 755),
        _leg("SELL", 772, "call"), _leg("BUY", 777, "call"),
    ]
    assert not _structure_consistent("IRON_CONDOR", legs)


def test_mixed_types_rejected():
    legs = [_leg("SELL", 762), _leg("BUY", 758, "call")]
    assert not _structure_consistent("BULL_PUT_SPREAD", legs)


# ------------------------------------------------------------- unit metrics
def test_unit_metrics_credit_spread():
    ml, mp, net = _unit_metrics(
        "BULL_PUT_SPREAD",
        [_leg("SELL", 762, mid=2.0), _leg("BUY", 758, mid=1.0)],
    )
    assert net == 1.0
    assert mp == 100.0
    assert ml == 300.0


def test_unit_metrics_debit_spread_no_keyerror():
    """Regression: DEBIT_SPREAD used to KeyError on l['leg']['strike']."""
    ml, mp, net = _unit_metrics(
        "DEBIT_SPREAD",
        [_leg("BUY", 764, mid=2.0), _leg("SELL", 760, mid=1.0)],
    )
    assert net == -1.0
    assert ml == 200.0
    assert mp == 200.0


def test_unit_metrics_long_option():
    ml, mp, net = _unit_metrics("LONG_PUT", [_leg("BUY", 750, mid=1.5)])
    assert net == -1.5
    assert ml == 150.0
    assert mp == float("inf")


def test_spread_strategy_is_rejected_by_long_option_policy():
    result = run_rule_checks(
        {"strategy_type": "BULL_PUT_SPREAD", "legs": []},
        [],
        SimpleNamespace(buying_power=100000, equity=100000),
        exposure_used_pct=0,
        open_positions_count=0,
    )

    assert result.checks["entry_style_allowed"] is False


def test_long_option_risk_uses_executable_ask_for_buy_cost():
    contract = SimpleNamespace(strike=750, opt_type="call", mid=1.0, ask=1.2, bid=0.8)
    result = _unit_metrics("LONG_CALL", [{"action": "BUY", "_contract": contract}])
    assert result[0] == 120.0


def test_long_premium_is_rejected_when_volatility_conflicts_with_partial_trend():
    from agents.risk_manager_agent import decide

    chain = [SimpleNamespace(
        symbol="X", strike=100, opt_type="call", mid=1.0, ask=1.0, bid=1.0,
        expiry="2026-09-03", spread_pct=0.01, delta=0.5, dte=5,
    )]
    result = decide(
        {"strategy_type": "LONG_CALL", "legs": [{"action": "BUY", "symbol": "X"}]},
        chain,
        SimpleNamespace(buying_power=100000, equity=100000),
        exposure_used_pct=0,
        open_positions_count=0,
        technical_report={"alignment": "PARTIAL"},
        volatility_report={"premium_bias": "SELL_PREMIUM"},
        use_llm_sanity=False,
    )

    assert result["decision"] == "REJECTED"
    assert result["checks"]["premium_alignment"] is False


def test_long_option_does_not_snap_unknown_symbol_to_another_contract():
    from agents.risk_manager_agent import run_rule_checks

    chain = [SimpleNamespace(
        symbol="REAL", strike=100, opt_type="call", mid=1.0,
        ask=1.0, bid=1.0, expiry="2026-09-03", spread_pct=0.01,
        delta=0.5, dte=5,
    )]
    result = run_rule_checks(
        {
            "strategy_type": "LONG_CALL",
            "legs": [{
                "action": "BUY", "symbol": "FAKE", "type": "call",
                "strike": 100, "expiry": "2026-09-03",
            }],
        },
        chain,
        SimpleNamespace(buying_power=100000, equity=100000),
        exposure_used_pct=0,
        open_positions_count=0,
    )

    assert result.checks["legs_valid"] is False


def test_symbol_exposure_limit_is_checked_independently():
    from agents.risk_manager_agent import run_rule_checks

    contract = SimpleNamespace(
        symbol="X", strike=100, opt_type="call", mid=20.0, ask=20.0, bid=20.0,
        expiry="2026-09-03", spread_pct=0.01, delta=0.5, dte=5,
    )
    result = run_rule_checks(
        {"strategy_type": "LONG_CALL", "legs": [{"action": "BUY", "symbol": "X"}]},
        [contract],
        SimpleNamespace(buying_power=100000, equity=100000),
        exposure_used_pct=0,
        open_positions_count=1,
        symbol_exposure_used_pct=14.0,
    )

    assert result.checks["exposure_within_limit"] is False


def test_single_leg_budget_uses_half_percent_trade_budget():
    contract = SimpleNamespace(
        symbol="X", strike=100, opt_type="call", mid=6.0, ask=6.0, bid=6.0,
        expiry="2026-09-03", spread_pct=0.01, delta=0.5, dte=5,
    )
    result = run_rule_checks(
        {"strategy_type": "LONG_CALL", "legs": [{"action": "BUY", "symbol": "X"}]},
        [contract],
        SimpleNamespace(buying_power=100000, equity=100000),
        exposure_used_pct=0,
        open_positions_count=0,
        max_loss_pct=0.005,
    )

    assert result.checks["max_loss_within_limit"] is False
