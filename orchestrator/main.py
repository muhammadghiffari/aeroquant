import sys
import os
import time
import requests
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.bull_put_analyst import BullPutAnalyst
from risk_management.risk_gate import RiskGate, RiskGateException
from execution.alpaca_executor import AlpacaExecutor

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

def run_cycle():
    print("==================================================")
    print("Memulai Trading Cycle: Bull Put Spread Harvester")
    print("==================================================")
    
    symbol = "SPY"
    
    # 1. Analisa LLM
    analyst = BullPutAnalyst()
    try:
        proposal = analyst.analyze_and_propose(symbol)
        print("\n[LLM PROPOSAL]")
        print(proposal.model_dump_json(indent=2))
    except Exception as e:
        msg = f"❌ [ERROR] Gagal mendapatkan proposal dari LLM: {e}"
        print(msg)
        send_telegram_alert(msg)
        return

    # 2. Risk Gate (Hard Requirement)
    gate = RiskGate(max_quantity=10)
    try:
        is_safe = gate.validate_proposal(proposal)
    except RiskGateException as e:
        msg = f"🚨 [RISK GATE BLOCKED] Proposal ditolak!\nAlasan: {e}"
        print(msg)
        send_telegram_alert(msg)
        return
    except Exception as e:
        msg = f"❌ [ERROR] Risk Gate gagal memproses: {e}"
        print(msg)
        send_telegram_alert(msg)
        return

    # 3. Eksekusi
    if is_safe:
        executor = AlpacaExecutor()
        success = executor.execute_bull_put_spread(proposal)
        
        if success:
            msg = f"✅ [TRADE EXECUTED] Bull Put Spread untuk {symbol} berhasil dieksekusi!\nShort Strike: {proposal.short_strike}\nLong Strike: {proposal.long_strike}"
            print(msg)
            send_telegram_alert(msg)
        else:
            msg = f"❌ [EXECUTION FAILED] Gagal mengirim order ke Alpaca."
            print(msg)
            send_telegram_alert(msg)

if __name__ == "__main__":
    load_dotenv()
    
    # Check if run in loop mode (e.g. for systemd)
    if "--loop" in sys.argv:
        print("Starting in LOOP mode...")
        send_telegram_alert("🚀 Bot Bull Put Spread (Amil) Started in Loop Mode!")
        while True:
            run_cycle()
            # Sleep 1 jam sebelum analisa lagi
            time.sleep(3600)
    else:
        # One-off run
        run_cycle()
