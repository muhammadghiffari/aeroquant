from pydantic import BaseModel, Field
from datetime import date

class TradeProposal(BaseModel):
    """
    Rekomendasi terstruktur dari agen LLM untuk mengeksekusi Bull Put Spread.
    Sesuai Aturan #5, ini HANYA proposal, dan harus lolos Risk Gate sebelum eksekusi.
    """
    symbol: str = Field(description="Simbol aset underlying, misalnya SPY")
    expiry: date = Field(description="Tanggal kedaluwarsa options (YYYY-MM-DD)")
    short_strike: float = Field(description="Strike price untuk opsi put yang dijual (Short Put). Harus lebih tinggi dari long_strike.")
    long_strike: float = Field(description="Strike price untuk opsi put yang dibeli (Long Put). Harus lebih rendah dari short_strike.")
    quantity: int = Field(description="Jumlah kontrak spread yang akan dieksekusi", ge=1)
    conviction_score: int = Field(description="Keyakinan LLM terhadap trade ini (1-100)", ge=1, le=100)
    reasoning: str = Field(description="Penjelasan singkat mengapa strike ini dipilih")
