import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.trade import TradeProposal

class RiskGateException(Exception):
    """Exception raised for trade proposals that fail risk checks."""
    pass

class RiskGate:
    def __init__(self, max_quantity: int = 5):
        self.max_quantity = max_quantity

    def validate_proposal(self, proposal: TradeProposal) -> bool:
        """
        Validasi deterministik (tanpa campur tangan LLM) untuk memastikan
        proposal mematuhi Aturan #4: Tidak boleh ada naked options.
        """
        print(f"[RISK GATE] Memvalidasi proposal trade untuk {proposal.symbol}...")
        
        # 1. Validasi Strategi Bull Put Spread
        if proposal.short_strike <= proposal.long_strike:
            raise RiskGateException(
                f"BULL PUT SPREAD INVALID: Short strike ({proposal.short_strike}) "
                f"harus lebih besar dari Long strike ({proposal.long_strike})."
            )
        
        # 2. Validasi Size/Quantity
        if proposal.quantity > self.max_quantity:
            raise RiskGateException(
                f"RISK LIMIT EXCEEDED: Kuantitas ({proposal.quantity}) "
                f"melebihi batas maksimal ({self.max_quantity})."
            )
            
        if proposal.quantity < 1:
            raise RiskGateException("Kuantitas tidak valid (harus >= 1).")
            
        # 3. Hitung Risk Nominal
        # Karena option dikalikan 100 per kontrak
        spread_width = proposal.short_strike - proposal.long_strike
        max_risk_per_contract = spread_width * 100
        total_max_risk = max_risk_per_contract * proposal.quantity
        
        print(f"[RISK GATE] Lebar Spread: ${spread_width:.2f}")
        print(f"[RISK GATE] Maksimal Risiko Modal: ${total_max_risk:.2f}")
        
        # Hard stop jika max risk terlalu besar (misalnya di atas $1000)
        # Sesuai prinsip proteksi modal
        if total_max_risk > 1000.0:
             raise RiskGateException(
                 f"RISK LIMIT EXCEEDED: Maksimal kerugian (${total_max_risk:.2f}) "
                 f"terlalu besar untuk satu trade."
             )
             
        # Jika semua lolos
        print("[RISK GATE] Validasi SUKSES. Trade dinyatakan Defined-Risk dan aman dieksekusi.")
        return True
