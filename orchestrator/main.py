import sys
import os
import time
import requests
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.agent_state import AgentState
from orchestrator.graph_nodes import node_fetch_data, node_analyze, node_risk_gate, node_execute

def send_telegram_alert(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[WARNING] Telegram credentials not found. Skipping alert.")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram alert: {e}")

def create_agent_graph():
    """Membangun LangGraph StateMachine."""
    # 1. Inisialisasi Graph
    workflow = StateGraph(AgentState)
    
    # 2. Tambahkan Nodes
    workflow.add_node("fetch_data", node_fetch_data)
    workflow.add_node("analyze", node_analyze)
    workflow.add_node("risk_gate", node_risk_gate)
    workflow.add_node("execute", node_execute)
    
    # 3. Definisikan Edges (Alur)
    workflow.set_entry_point("fetch_data")
    workflow.add_edge("fetch_data", "analyze")
    workflow.add_edge("analyze", "risk_gate")
    
    # Edge bersyarat dari risk_gate
    def should_execute(state: AgentState):
        if state.get("risk_passed"):
            return "execute"
        else:
            return "end"
            
    workflow.add_conditional_edges(
        "risk_gate",
        should_execute,
        {
            "execute": "execute",
            "end": END
        }
    )
    workflow.add_edge("execute", END)
    
    # 4. Tambahkan Checkpointer untuk State Memory
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    return app

def run_cycle(app):
    print("==================================================")
    print("Memulai Trading Cycle: Bull Put Spread (LangGraph)")
    print("==================================================")
    
    # Inisialisasi state awal
    initial_state = {
        "symbol": "SPY",
        "current_price": None,
        "market_context": None,
        "option_chain": None,
        "trade_proposal": None,
        "risk_passed": False,
        "risk_message": None,
        "execution_success": False,
        "execution_message": None
    }
    
    # Thread ID penting di LangGraph agar memori tersimpan untuk eksekusi ini
    config = {"configurable": {"thread_id": "amil-trading-thread-1"}}
    
    try:
        # Jalankan graph sampai selesai
        result = app.invoke(initial_state, config=config)
        
        # Evaluasi Hasil Akhir
        if result.get("execution_success"):
            msg = f"[TRADE EXECUTED] {result.get('execution_message')}"
            print(msg)
            send_telegram_alert(msg)
        else:
            if not result.get("risk_passed"):
                msg = f"[BLOCKED] {result.get('risk_message')}"
                print(msg)
                send_telegram_alert(msg)
            else:
                msg = f"[ERROR] Eksekusi Gagal: {result.get('execution_message')}"
                print(msg)
                send_telegram_alert(msg)
                
    except Exception as e:
        msg = f"[FATAL ERROR] Siklus gagal: {e}"
        print(msg)
        send_telegram_alert(msg)

if __name__ == "__main__":
    load_dotenv()
    app = create_agent_graph()
    
    if "--loop" in sys.argv:
        print("Starting in LOOP mode with LangGraph...")
        send_telegram_alert("🚀 Bot LangGraph Bull Put Spread (Amil) Started in Loop Mode!")
        while True:
            run_cycle(app)
            time.sleep(3600)
    else:
        run_cycle(app)
