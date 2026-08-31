from typing import TypedDict, Any, List, Optional
from models.trade import TradeProposal

class AgentState(TypedDict):
    """
    State dictionary yang akan dioper antar node di dalam LangGraph.
    Berfungsi sebagai memori agen selama satu siklus trading.
    """
    # Input
    symbol: str
    
    # Hasil dari Node: Fetch Data
    current_price: Optional[float]
    market_context: Optional[str]
    option_chain: Optional[List[dict]]
    target_expiry: Optional[str]
    
    # Hasil dari Node: Analyze
    trade_proposal: Optional[TradeProposal]
    
    # Hasil dari Node: Risk Gate
    risk_passed: bool
    risk_message: Optional[str]
    
    # Hasil dari Node: Execute
    execution_success: bool
    execution_message: Optional[str]
