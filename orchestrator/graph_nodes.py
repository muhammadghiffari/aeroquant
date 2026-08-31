import sys
import os
import json
from datetime import date, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.agent_state import AgentState
from data_engine.market_data import MarketDataEngine
from model_gateway import ModelGateway
from models.trade import TradeProposal
from risk_management.risk_gate import RiskGate, RiskGateException
from execution.alpaca_executor import AlpacaExecutor

def node_fetch_data(state: AgentState) -> AgentState:
    print("[NODE] Mengambil data market...")
    engine = MarketDataEngine()
    symbol = state["symbol"]
    
    current_price = engine.get_underlying_price(symbol)
    context = engine.get_market_context(symbol)
    
    # Target expiry minggu depan
    target_expiry = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    chain = engine.get_option_chain_summary(symbol, target_expiry)
    
    return {
        **state,
        "current_price": current_price,
        "market_context": context,
        "target_expiry": target_expiry,
        "option_chain": chain
    }

def node_analyze(state: AgentState) -> AgentState:
    print("[NODE] LLM menganalisa data...")
    gateway = ModelGateway()
    
    system_prompt = (
        "You are an expert options trading AI. Your task is to propose a Bull Put Spread strategy. "
        "A Bull Put Spread involves selling a put option at a higher strike and buying a put option at a lower strike, both with the same expiration. "
        "You must select the strikes from the provided options chain data."
    )
    
    user_prompt = (
        f"Underlying Asset: {state['symbol']}\n"
        f"Current Price: ${state['current_price']}\n"
        f"Market Context: {state['market_context']}\n"
        f"Target Expiry: {state['target_expiry']}\n"
        f"Available Put Options:\n{json.dumps(state['option_chain'], indent=2)}\n\n"
        "Based on this data, propose a Bull Put Spread. Choose a short_strike and a long_strike. "
        "Make sure short_strike > long_strike. "
        "Set quantity to 1 for this test."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        parsed_proposal, provider = gateway.generate(
            role="bull_put_analysis",
            policy="fast_analysis",
            messages=messages,
            response_model=TradeProposal,
            correlation_id="amil-langgraph-01"
        )
        print(f"[NODE] LLM Analysis complete by {provider}.")
        return {**state, "trade_proposal": parsed_proposal}
    except Exception as e:
        print(f"[NODE] LLM Analysis ERROR: {e}")
        # Default empty proposal on failure to trigger risk block
        return {**state, "trade_proposal": None}

def node_risk_gate(state: AgentState) -> AgentState:
    print("[NODE] Risk Gate memeriksa proposal...")
    proposal = state.get("trade_proposal")
    
    if not proposal:
        return {
            **state, 
            "risk_passed": False, 
            "risk_message": "Tidak ada proposal dari LLM (gagal parsing)."
        }
        
    gate = RiskGate(max_quantity=5)
    try:
        is_safe = gate.validate_proposal(proposal)
        return {
            **state,
            "risk_passed": is_safe,
            "risk_message": "Proposal lolos Risk Gate."
        }
    except RiskGateException as e:
        print(f"[NODE] Risk Gate BLOCKED: {e}")
        return {
            **state,
            "risk_passed": False,
            "risk_message": str(e)
        }

def node_execute(state: AgentState) -> AgentState:
    print("[NODE] Eksekusi order...")
    # Node ini harusnya hanya terpanggil jika risk_passed == True, 
    # namun kita double check untuk keamanan.
    if not state.get("risk_passed"):
        print("[NODE] Eksekusi dibatalkan karena risk_passed = False")
        return {**state, "execution_success": False, "execution_message": "Blocked by Risk Gate"}
        
    executor = AlpacaExecutor()
    proposal = state["trade_proposal"]
    
    success = executor.execute_bull_put_spread(proposal)
    if success:
        msg = f"Trade berhasil dieksekusi: {proposal.symbol} (Short {proposal.short_strike}, Long {proposal.long_strike})"
    else:
        msg = "Gagal mengirim order ke Alpaca."
        
    return {
        **state,
        "execution_success": success,
        "execution_message": msg
    }
