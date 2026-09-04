"""LangGraph adapter for isolated per-symbol decision cycles.

The node receives a deterministic callback from the orchestrator. LLM agents
remain data-only components; the callback is the only path that can reach the
deterministic risk and execution services.
"""
from typing import Callable, TypedDict

from langgraph.graph import END, StateGraph


class SymbolCycleState(TypedDict, total=False):
    symbol: str
    processor: Callable[[str], dict]
    result: dict


def _process_symbol_node(state: SymbolCycleState) -> dict:
    return {"result": state["processor"](state["symbol"])}


def run_symbol_graph(symbol: str, processor: Callable[[str], dict]) -> dict:
    """Execute one isolated symbol decision through LangGraph."""
    graph = StateGraph(SymbolCycleState)
    graph.add_node("process_symbol", _process_symbol_node)
    graph.set_entry_point("process_symbol")
    graph.add_edge("process_symbol", END)
    result = graph.compile().invoke({"symbol": symbol, "processor": processor})
    return result["result"]
