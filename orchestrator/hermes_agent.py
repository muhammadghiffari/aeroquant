import os
import sys
import time
import json
import argparse
from datetime import date, timedelta

# Pastikan path import benar
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_engine.market_data import MarketDataEngine
from model_gateway import ModelGateway
from models.trade import TradeProposal
from risk_management.risk_gate import RiskGate, RiskGateException
from execution.alpaca_executor import AlpacaExecutor

def send_telegram_alert(message: str):
    # Simulasi telegram alert seperti di orchestrator lama
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("[WARNING] Telegram credentials not found. Skipping alert.")
        return
    import requests
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
    except Exception as e:
        print(f"[ERROR] Failed to send telegram alert: {e}")

class HermesAgentOrchestrator:
    def __init__(self, symbol: str = "SPY"):
        self.symbol = symbol
        self.market_engine = MarketDataEngine()
        self.gateway = ModelGateway()
        self.executor = AlpacaExecutor()
        
    def run_trading_cycle(self):
        print(f"\n{'='*50}")
        print(f"Memulai Trading Cycle: Bull Put Spread (Hermes Agent)")
        print(f"{'='*50}")
        
        # 1. Fetch Market Data
        print("[NODE] Mengambil data market...")
        current_price = self.market_engine.get_underlying_price(self.symbol)
        market_context = self.market_engine.get_market_context(self.symbol)
        
        target_expiry = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
        option_chain = self.market_engine.get_option_chain_summary(self.symbol, target_expiry)
        
        if option_chain and "real_expiration" in option_chain[0]:
            target_expiry = option_chain[0]["real_expiration"]
            
        # 2. Analyze with Hermes (via Gateway)
        print("[NODE] LLM menganalisa data...")
        system_prompt = (
            "You are an expert options trading AI known as Hermes. Your task is to propose a Bull Put Spread strategy. "
            "A Bull Put Spread involves selling a put option at a higher strike and buying a put option at a lower strike, both with the same expiration. "
            "You must select the strikes from the provided options chain data."
        )
        
        user_prompt = (
            f"Underlying Asset: {self.symbol}\n"
            f"Current Price: ${current_price}\n"
            f"Market Context: {market_context}\n"
            f"Target Expiry: {target_expiry}\n"
            f"Available Put Options:\n{json.dumps(option_chain, indent=2)}\n\n"
            "Based on this data, propose a Bull Put Spread. Choose a short_strike and a long_strike. "
            "Make sure short_strike > long_strike. "
            "Set quantity to 1 for this test."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        proposal = None
        try:
            parsed_proposal, provider = self.gateway.generate(
                role="bull_put_analysis",
                policy="fast_analysis",
                messages=messages,
                response_model=TradeProposal,
                correlation_id="amil-hermes-01"
            )
            print(f"[NODE] LLM Analysis complete by {provider}.")
            proposal = parsed_proposal
        except Exception as e:
            print(f"[NODE] LLM Analysis ERROR: {e}")
            
        # 3. Risk Gate
        print("[NODE] Risk Gate memeriksa proposal...")
        if not proposal:
            print("[BLOCKED] Tidak ada proposal dari LLM (gagal parsing).")
            send_telegram_alert("⚠️ Hermes Agent Cycle Gagal: LLM tidak mengembalikan proposal yang valid.")
            return False
            
        gate = RiskGate(max_quantity=5)
        try:
            is_safe = gate.validate_proposal(proposal)
            print("[RISK GATE] Validasi SUKSES. Trade dinyatakan Defined-Risk dan aman dieksekusi.")
        except RiskGateException as e:
            print(f"[NODE] Risk Gate BLOCKED: {e}")
            send_telegram_alert(f"🛑 Trade Ditolak oleh Risk Gate: {e}")
            return False
            
        # 4. Execute
        print("[NODE] Eksekusi order...")
        success = self.executor.execute_bull_put_spread(proposal)
        if success:
            msg = f"✅ Hermes Agent sukses mengeksekusi trade: {proposal.symbol} (Short {proposal.short_strike}, Long {proposal.long_strike})"
            print(f"[EXECUTOR] {msg}")
            send_telegram_alert(msg)
            return True
        else:
            msg = "❌ Eksekusi Gagal: Gagal mengirim order ke Alpaca."
            print(f"[ERROR] {msg}")
            send_telegram_alert(msg)
            return False

def main():
    parser = argparse.ArgumentParser(description="Hermes Agent Trading Orchestrator")
    parser.add_argument("--loop", action="store_true", help="Jalankan secara terus menerus (1 jam sekali)")
    parser.add_argument("--symbol", type=str, default="SPY", help="Simbol saham yang ditradingkan")
    args = parser.parse_args()
    
    agent = HermesAgentOrchestrator(symbol=args.symbol)
    
    if args.loop:
        print("Starting in LOOP mode with Hermes Agent...")
        send_telegram_alert("🚀 Bot Hermes Agent Bull Put Spread (Amil) Started in Loop Mode!")
        try:
            while True:
                agent.run_trading_cycle()
                print("\n[LOOP] Tidur selama 1 jam sebelum cycle berikutnya...")
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\n[EXIT] Hermes Agent dihentikan oleh user.")
            send_telegram_alert("⏹️ Bot Hermes Agent dihentikan.")
    else:
        agent.run_trading_cycle()

if __name__ == "__main__":
    main()
