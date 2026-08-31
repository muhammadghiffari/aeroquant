import sys
import os
import json
from datetime import date, timedelta

# Add parent directory to path so we can import model_gateway and models
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_gateway import ModelGateway
from models.trade import TradeProposal
from data_engine.market_data import MarketDataEngine

class BullPutAnalyst:
    def __init__(self):
        self.gateway = ModelGateway()
        self.market_data = MarketDataEngine()

    def analyze_and_propose(self, symbol: str) -> TradeProposal:
        """
        Mengambil data market, mengirim prompt ke LLM (Featherless Qwen3-32B),
        dan mengembalikan objek TradeProposal terstruktur.
        """
        # 1. Gather Context
        current_price = self.market_data.get_underlying_price(symbol)
        context = self.market_data.get_market_context(symbol)
        
        # Asumsikan target expiry minggu depan (7 hari dari sekarang)
        target_expiry = (date.today() + timedelta(days=7)).strftime("%Y-%MM-%DD")
        
        chain = self.market_data.get_option_chain_summary(symbol, target_expiry)
        
        # 2. Build Prompt
        system_prompt = (
            "You are an expert options trading AI. Your task is to propose a Bull Put Spread strategy. "
            "A Bull Put Spread involves selling a put option at a higher strike and buying a put option at a lower strike, both with the same expiration. "
            "You must select the strikes from the provided options chain data."
        )
        
        user_prompt = (
            f"Underlying Asset: {symbol}\n"
            f"Current Price: ${current_price}\n"
            f"Market Context: {context}\n"
            f"Target Expiry: {target_expiry}\n"
            f"Available Put Options:\n{json.dumps(chain, indent=2)}\n\n"
            "Based on this data, propose a Bull Put Spread. Choose a short_strike and a long_strike. "
            "Make sure short_strike > long_strike. "
            "Set quantity to 1 for this test."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        print(f"[*] Agent is analyzing data for {symbol}...")
        
        # 3. Call Model Gateway
        # Menggunakan policy 'fast_analysis' yang akan pakai Anthropic (jika diset) atau fallback ke Featherless.
        # Jika Anthropic API Key kosong atau invalid (untuk testing), dia akan otomatis fallback ke Featherless.
        parsed_proposal, provider = self.gateway.generate(
            role="bull_put_analysis",
            policy="fast_analysis",
            messages=messages,
            response_model=TradeProposal,
            correlation_id="amil-test-01"
        )
        
        print(f"[*] Agent analysis complete. Served by: {provider}")
        return parsed_proposal

if __name__ == "__main__":
    analyst = BullPutAnalyst()
    proposal = analyst.analyze_and_propose("SPY")
    print(proposal.model_dump_json(indent=2))
