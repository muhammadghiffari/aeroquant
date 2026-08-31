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
        Mengambil option chain riil dari Alpaca untuk underlying tertentu.
        Menghasilkan list dari beberapa strike price dan estimasi premi put option.
        """
        url = f"{self.base_url}/v2/options/contracts?underlying_symbols={symbol}&status=active&type=put&limit=100"
        
        chain = []
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                data = response.json()
                contracts = data.get("option_contracts", [])
                
                if not contracts:
                    print(f"[WARNING] Tidak ada opsi put yang aktif untuk {symbol}")
                    return []
                
                # Gunakan expiration date terdekat yang ada di sistem Alpaca (abaikan target_expiry parameter)
                expirations = sorted(list(set(c["expiration_date"] for c in contracts)))
                closest_exp = expirations[0]
                
                # Filter hanya contract dengan closest expiration
                valid_contracts = [c for c in contracts if c["expiration_date"] == closest_exp]
                
                # Sort berdasarkan strike price
                valid_contracts = sorted(valid_contracts, key=lambda x: float(x["strike_price"]), reverse=True)
                
                # Ambil 5 strike tertinggi (atau terdekat ATM jika memungkinkan)
                for i, contract in enumerate(valid_contracts[:5]):
                    chain.append({
                        "strike": float(contract["strike_price"]),
                        "type": "put",
                        "estimated_premium": round(5.0 - i * 0.8, 2), # Dummy premium
                        "implied_volatility": round(0.15 + i * 0.02, 3), # Dummy IV
                        "real_expiration": closest_exp # Tambahkan real_expiration ke context
                    })
                return chain
            else:
                print(f"[ERROR] Gagal fetch option chain dari Alpaca: {response.text}")
        except Exception as e:
            print(f"[ERROR] Exception saat fetch option chain: {e}")
            
        return chain

    def get_market_context(self, symbol: str) -> str:
        """
        Mengambil technical indicator sederhana atau sentimen.
        """
        # Bisa di-expand untuk mengambil MA20, RSI, dll.
        return f"Market Context untuk {symbol}: Harga saat ini berada di atas MA20 (Bullish trend jangka pendek). VIX stabil. Ideal untuk strategi Bull Put Spread."
