import os
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

class MarketDataEngine:
    def __init__(self):
        self.api_key = os.environ.get("APCA_API_KEY_ID")
        self.api_secret = os.environ.get("APCA_API_SECRET_KEY")
        self.base_url = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
        
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json"
        }

    def get_underlying_price(self, symbol: str) -> float:
        """
        Mendapatkan harga terkini dari underlying asset (contoh: SPY).
        Menggunakan endpoint Market Data v2.
        """
        # Dalam implementasi riil, pakai endpoint Alpaca Market Data:
        # https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest
        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                data = response.json()
                # Return harga ask sebagai estimasi current price
                return float(data['quote']['ap'])
        except Exception as e:
            print(f"Error fetching price for {symbol}: {e}")
        
        # Fallback dummy price jika error/belum subscribe data feed
        print(f"[WARNING] Using fallback price for {symbol}")
        return 500.0

    def get_option_chain_summary(self, symbol: str, target_expiry: str) -> list[dict]:
        """
        Mengambil sampel option chain untuk underlying tertentu.
        Menghasilkan list dari beberapa strike price dan estimasi premi put option.
        """
        # Dalam implementasi riil, panggil Alpaca Options Data API atau MCP.
        # Karena endpoint options Alpaca masih beta/terpisah, kita simulasikan 
        # chain di sekitar current price.
        current_price = self.get_underlying_price(symbol)
        
        # Dummy data untuk memberikan konteks ke LLM
        # Asumsikan strikes 1-5% di bawah harga saat ini
        strikes = [
            round(current_price * 0.99, 0),
            round(current_price * 0.98, 0),
            round(current_price * 0.97, 0),
            round(current_price * 0.96, 0),
            round(current_price * 0.95, 0),
        ]
        
        chain = []
        for i, strike in enumerate(strikes):
            chain.append({
                "strike": strike,
                "type": "put",
                "estimated_premium": round(5.0 - i * 0.8, 2), # Semakin jauh OTM, semakin murah
                "implied_volatility": round(0.15 + i * 0.02, 3)
            })
            
        return chain

    def get_market_context(self, symbol: str) -> str:
        """
        Mengambil technical indicator sederhana atau sentimen.
        """
        # Bisa di-expand untuk mengambil MA20, RSI, dll.
        return f"Market Context untuk {symbol}: Harga saat ini berada di atas MA20 (Bullish trend jangka pendek). VIX stabil. Ideal untuk strategi Bull Put Spread."
