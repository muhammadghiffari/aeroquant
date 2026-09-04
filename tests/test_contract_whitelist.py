from datetime import datetime, timedelta, timezone

from data_engine.option_data import (
    OptionContract,
    build_candidate_whitelist,
    build_shadow_candidate_list,
)
from agents.risk_manager_agent import validate_quant_entry
from agents.news_earnings_agent import NewsEarningsAgent
from agents.strategy_decision_agent import StrategyDecisionAgent, validate_candidate_choice


def _contract(symbol, opt_type, *, dte=14, delta=0.58, bid=2.0, ask=2.1, spread=0.048):
    return OptionContract(
        symbol=symbol, underlying="SPY", expiry="2026-09-19", opt_type=opt_type,
        strike=500.0, bid=bid, ask=ask, mid=(bid + ask) / 2,
        spread_pct=spread, iv=0.2, delta=delta, dte=dte,
    )


def _signal(direction="BULLISH", probability=0.7, move=8.0):
    return {
        "direction": direction,
        "strategy_type": "LONG_CALL" if direction == "BULLISH" else "LONG_PUT",
        "actionable": True,
        "probability_lower_bound": probability,
        "expected_move_abs": move,
    }


def test_whitelist_contains_only_directional_liquid_positive_ev_contracts():
    call = _contract("SPY260919C00500000", "call")
    put = _contract("SPY260919P00500000", "put")
    wide = _contract("SPY260919C00505000", "call", spread=0.20)

    candidates = build_candidate_whitelist([call, put, wide], _signal(), spot=500.0)

    assert [item["symbol"] for item in candidates]
    assert all(item["strategy_type"] == "LONG_CALL" for item in candidates)
    assert all(item["expected_value_after_costs"] > 0 for item in candidates)
    assert "SPY260919P00500000" not in {item["symbol"] for item in candidates}
    assert "SPY260919C00505000" not in {item["symbol"] for item in candidates}


def test_whitelist_fails_closed_when_probability_gate_is_not_met():
    contract = _contract("SPY260919C00500000", "call")

    assert build_candidate_whitelist([contract], _signal(probability=0.50), spot=500.0) == []


def test_advisory_history_does_not_block_valid_live_candidate():
    contract = _contract("SPY260919C00500000", "call")
    signal = _signal(probability=0.42, move=8.0)
    signal.update({
        "probability": 0.42,
        "probability_floor": 0.50,
        "historical_confidence_advisory": True,
        "require_positive_ev": False,
    })

    candidates = build_candidate_whitelist([contract], signal, spot=500.0)

    assert candidates
    assert candidates[0]["probability"] == 0.42


def test_shadow_candidates_are_directional_but_do_not_require_profit_gate():
    call = _contract("SPY260919C00500000", "call")
    put = _contract("SPY260919P00500000", "put")

    candidates = build_shadow_candidate_list([call, put], "BULLISH")

    assert [item["symbol"] for item in candidates] == [call.symbol]
    assert candidates[0]["shadow"] is True


def test_whitelist_accepts_exact_probability_threshold():
    contract = _contract("SPY260919C00500000", "call")

    assert build_candidate_whitelist([contract], _signal(probability=0.60), spot=500.0)


def test_proxy_whitelist_uses_live_quote_as_context_not_positive_ev_gate():
    contract = _contract("SPY260919C00500000", "call")
    contract.bid_size = 30
    contract.ask_size = 80
    signal = _signal(probability=0.42, move=0.01)
    signal.update({
        "probability": 0.55,
        "probability_floor": 0.50,
        "probability_basis": "stock_history_setup_probability",
        "require_positive_ev": False,
    })

    candidates = build_candidate_whitelist([contract], signal, spot=500.0)

    assert candidates
    assert candidates[0]["probability"] == 0.55
    assert candidates[0]["probability_lower_bound"] == 0.42
    assert candidates[0]["probability_basis"] == "stock_history_setup_probability"
    assert candidates[0]["live_quote_activity"]["dominant_side"] == "ASK_HEAVY"


def test_whitelist_rejects_stale_quote():
    contract = _contract("SPY260919C00500000", "call")
    contract.quote_timestamp = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()

    assert build_candidate_whitelist([contract], _signal(), spot=500.0) == []


def test_llm_choice_must_match_exact_whitelisted_candidate_and_strategy():
    candidates = [{
        "candidate_id": "SPY260919C00500000",
        "symbol": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
    }]
    valid = {
        "candidate_id": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260919C00500000", "qty": 1}],
    }
    invalid = {
        "candidate_id": "SPY260919P00500000",
        "strategy_type": "LONG_PUT",
        "legs": [{"action": "BUY", "symbol": "SPY260919P00500000", "qty": 1}],
    }

    assert validate_candidate_choice(valid, candidates) is True
    assert validate_candidate_choice(invalid, candidates) is False


def test_strategy_schema_requires_quant_candidate_id():
    assert "candidate_id" in StrategyDecisionAgent.schema["required"]


def test_strategy_prompt_allows_explicit_shadow_only_analysis():
    assert "SHADOW_ONLY" in StrategyDecisionAgent.system_prompt


def test_strategy_prompt_explains_underlying_history_proxy_confidence():
    assert "GREEN_PROXY" in StrategyDecisionAgent.system_prompt
    assert "contract_confidence" in StrategyDecisionAgent.system_prompt


def test_news_agent_supports_grounded_critical_event_risk():
    assert "CRITICAL" in NewsEarningsAgent.schema["properties"]["event_risk"]["enum"]


def test_quant_entry_check_rejects_direction_or_candidate_mismatch():
    quant = {
        "entry_actionable": True,
        "direction": "BULLISH",
        "candidates": [{
            "candidate_id": "SPY260919C00500000",
            "symbol": "SPY260919C00500000",
            "strategy_type": "LONG_CALL",
        }],
    }
    valid = {
        "candidate_id": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260919C00500000", "qty": 1}],
    }
    wrong_direction = {**valid, "strategy_type": "LONG_PUT"}

    assert validate_quant_entry(valid, quant) == (True, "")
    assert validate_quant_entry(wrong_direction, quant)[0] is False


def test_quant_entry_uses_confirmed_confidence_direction():
    quant = {
        "entry_actionable": True,
        "direction": "WAIT",
        "confidence": {"state": "ENTER_CONFIRMED", "direction": "BULLISH"},
        "candidates": [{
            "candidate_id": "SPY260919C00500000",
            "symbol": "SPY260919C00500000",
            "strategy_type": "LONG_CALL",
        }],
    }
    proposal = {
        "candidate_id": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260919C00500000", "qty": 1}],
    }

    assert validate_quant_entry(proposal, quant) == (True, "")


def test_quant_entry_accepts_green_contract_confidence():
    quant = {
        "entry_actionable": True,
        "direction": "WAIT",
        "confidence": {"state": "GREEN", "direction": "BULLISH"},
        "candidates": [{
            "candidate_id": "SPY260919C00500000",
            "symbol": "SPY260919C00500000",
            "strategy_type": "LONG_CALL",
        }],
    }
    proposal = {
        "candidate_id": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260919C00500000", "qty": 1}],
    }

    assert validate_quant_entry(proposal, quant) == (True, "")


def test_quant_entry_accepts_explicit_green_underlying_history_proxy():
    quant = {
        "entry_actionable": True,
        "confidence": {"state": "GREEN_PROXY", "direction": "BULLISH", "source": "stock_bars"},
        "candidates": [{
            "candidate_id": "SPY260919C00500000",
            "symbol": "SPY260919C00500000",
            "strategy_type": "LONG_CALL",
        }],
    }
    proposal = {
        "candidate_id": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260919C00500000", "qty": 1}],
    }

    assert validate_quant_entry(proposal, quant) == (True, "")


def test_quant_entry_accepts_advisory_wait_see_confidence():
    quant = {
        "entry_actionable": True,
        "confidence": {
            "state": "WAIT_SEE", "direction": "BULLISH",
            "historical_confidence_advisory": True,
        },
        "candidates": [{
            "candidate_id": "SPY260919C00500000",
            "symbol": "SPY260919C00500000",
            "strategy_type": "LONG_CALL",
        }],
    }
    proposal = {
        "candidate_id": "SPY260919C00500000",
        "strategy_type": "LONG_CALL",
        "legs": [{"action": "BUY", "symbol": "SPY260919C00500000", "qty": 1}],
    }

    assert validate_quant_entry(proposal, quant) == (True, "")
