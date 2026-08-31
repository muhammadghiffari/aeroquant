import os
import requests
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.trade import TradeProposal

class AlpacaExecutor:
    def __init__(self):
        self.api_key = os.environ.get("APCA_API_KEY_ID")
        self.api_secret = os.environ.get("APCA_API_SECRET_KEY")
        self.base_url = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
        
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json",
            "content-type": "application/json"
        }

    def _generate_opra_symbol(self, symbol: str, expiry_str: str, strike: float, option_type: str = "P") -> str:
        """
        Helper untuk mengenerate OPRA symbol.
        Contoh: SPY 2024-08-30 Strike 500 Put -> SPY240830P00500000
        """
        # Format date dari YYYY-MM-DD ke YYMMDD
        date_obj = expiry_str.split("-")
        yymmdd = date_obj[0][2:] + date_obj[1] + date_obj[2]
        
        # Strike: dikali 1000, pad left dengan nol sampai 8 digit
        strike_int = int(strike * 1000)
        strike_str = str(strike_int).zfill(8)
        
        return f"{symbol.ljust(6, ' ').strip()}{yymmdd}{option_type}{strike_str}"

    def execute_bull_put_spread(self, proposal: TradeProposal):
        """
        Mengeksekusi multi-leg order (Bull Put Spread) ke Alpaca.
        Mengirimkan 1 order 'sell' untuk short leg dan 1 order 'buy' untuk long leg
        secara bersamaan (biasanya Alpaca mendukung advanced options order MLEGs
        atau kita bisa kirim individual jika MLEG belum disupport penuh).
        """
        short_symbol = self._generate_opra_symbol(proposal.symbol, str(proposal.expiry), proposal.short_strike)
        long_symbol = self._generate_opra_symbol(proposal.symbol, str(proposal.expiry), proposal.long_strike)
        
        print(f"[EXECUTOR] Menyiapkan order eksekusi untuk:")
        print(f"   - Short Leg (Jual): {short_symbol}")
        print(f"   - Long Leg (Beli):  {long_symbol}")
        
        # Di Alpaca, kita bisa mengirimkan multi-leg order class 'mleg'
        # Namun untuk simplicity (dan karena API options MLEG Alpaca masih beta), 
        # kita submit 2 order individual (limit/market) 
        # PERINGATAN: Di live trading, harus di-submit sbg MLEG untuk menghindar execution risk.
        # Untuk keperluan Hackathon ini, kita simulasikan payload MLEG.

        payload = {
            "class": "mleg",
            "symbol": proposal.symbol,
            "qty": str(proposal.quantity),
            "order_class": "mleg",
            "legs": [
                {
                    "symbol": short_symbol,
                    "ratio_qty": "1",
                    "side": "sell"
                },
                {
                    "symbol": long_symbol,
                    "ratio_qty": "1",
                    "side": "buy"
                }
            ],
            "type": "market",
            "time_in_force": "day"
        }

        url = f"{self.base_url}/v2/orders"
        
        try:
            print("[EXECUTOR] Mengirim order ke Alpaca API...")
            response = requests.post(url, json=payload, headers=self.headers)
            
            if response.status_code in [200, 201]:
                print("[EXECUTOR] ✅ Order Berhasil Diterima oleh Alpaca!")
                print(response.json())
                return True
            else:
                print(f"[EXECUTOR] ❌ Order Gagal! {response.status_code}")
                print(response.text)
                return False
                
        except Exception as e:
            print(f"[EXECUTOR] Terjadi error saat mengeksekusi order: {e}")
            return False
