"""LangGraph coordination tests without LLM or broker network calls."""
from orchestrator.langgraph_cycle import run_symbol_graph


def test_graph_invokes_isolated_symbol_processor_once():
    calls = []

    def process(symbol):
        calls.append(symbol)
        return {"symbol": symbol, "action": "WAIT"}

    result = run_symbol_graph("SPY", process)

    assert calls == ["SPY"]
    assert result == {"symbol": "SPY", "action": "WAIT"}
