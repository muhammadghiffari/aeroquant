# AGENTS.md
# Spesifikasi Agent & Flow — Autonomous Multi-Agent Options Trading System v1.0
> Dokumen ini mendefinisikan seluruh agent dalam sistem, domain analisis, sumber data, format report, dan alur kerja lengkap dari input pasar sampai eksekusi order.
> Dibaca bersama: `PRD.md`
> Instrumen: US Equity Options — via Alpaca Trading API & Market Data API

---

## Daftar Isi

1. [Filosofi & Pembagian Peran](#1-filosofi--pembagian-peran)
2. [Hierarki Agent](#2-hierarki-agent)
3. [Format Report Standar](#3-format-report-standar)
4. [Quant Engine (Non-LLM)](#4-quant-engine-non-llm)
5. [UnderlyingTrendAgent](#5-underlyingtrendagent)
6. [VolatilityAgent](#6-volatilityagent)
7. [NewsEarningsAgent](#7-newsearningsagent)
8. [Technical Manager](#8-technical-manager)
9. [Context Manager](#9-context-manager)
10. [Strategy Decision Agent — Chief Supervisor](#10-strategy-decision-agent--chief-supervisor)
11. [Risk Manager Agent](#11-risk-manager-agent)
12. [Execution Agent](#12-execution-agent)
13. [Position Monitor](#13-position-monitor)
14. [Data Flow Lengkap (End-to-End)](#14-data-flow-lengkap-end-to-end)
15. [Contoh Skenario Lengkap](#15-contoh-skenario-lengkap)
16. [Error Handling & Fallback](#16-error-handling--fallback)
17. [Referensi](#17-referensi)

---

## 1. Filosofi & Pembagian Peran

Sistem ini memisahkan tegas dua jenis "kecerdasan":

- **Quant Engine (non-LLM):** menghasilkan angka — presisi, deterministik, dapat diverifikasi ulang secara matematis. Tidak ada LLM yang terlibat di sini. Ini adalah fondasi data.
- **LLM Agents:** menginterpretasikan angka dari Quant Engine, menggabungkan dengan konteks kualitatif (berita, earnings, kondisi pasar), dan **mengambil keputusan otonom** — bukan mengikuti rule if-else statis.

Prinsip ini penting untuk dipahami setiap agent: **LLM tidak pernah diminta menghitung ulang angka teknikal (itu domain Quant Engine)** — LLM tugasnya memberi konteks dan memilih satu `candidate_id` dari whitelist Quant, atau `WAIT`.

Sistem berjalan sebagai **hierarchical sequential pipeline**. Entry menjalankan
Quant → sub-agent → manager → Chief → Risk → Execution, sedangkan position
monitor menangani exit secara deterministik. BTC hanya shadow telemetry dan tidak
memiliki hak menghasilkan, mengubah, atau memperbesar trade.

Runtime saat ini menggunakan Featherless OpenAI-compatible API: agent light
memakai `zai-org/GLM-5.2`, sedangkan agent heavy memakai
`zai-org/GLM-5.3-Flash`. Semua calls tetap stateless dan schema-bound.

---

## 2. Hierarki Agent

```
Alpaca Market Data API ─────────────────── Alpaca News API
       │                                          │
       ▼                                          │
┌─────────────────────────┐                       │
│   Quant Engine           │                       │
│   (Python murni, no LLM) │                       │
│                          │                       │
│  IV Rank, IV Percentile  │                       │
│  HV/IV Spread            │                       │
│  Expected Move           │                       │
│  Skew, PoP proxy         │                       │
│  Trend Z-score           │                       │
└────────────┬─────────────┘                       │
             │ quant_report (numeric, Opsi B)       │
   ┌─────────┴──────────┐                          │
   ▼                     ▼                          ▼
┌───────────────┐  ┌───────────────┐   ┌────────────────────┐
│ Underlying     │  │ Volatility    │   │ NewsEarnings        │
│ TrendAgent     │  │ Agent         │   │ Agent                │
│ (LLM)          │  │ (LLM)         │   │ (LLM)                │
└───────┬────────┘  └───────┬───────┘   └──────────┬───────────┘
        │                    │                       │
        └─────────┬──────────┘                       │
                   ▼                                  ▼
        ┌─────────────────────┐            ┌──────────────────────┐
        │ Technical Manager    │            │ Context Manager        │
        │ (LLM)                │            │ (LLM)                  │
        └──────────┬────────────┘            └───────────┬────────────┘
                    │ technical_report                    │ context_report
                    └────────────────┬─────────────────────┘
                                     ▼
                    ┌───────────────────────────────┐
                    │  Strategy Decision Agent        │
                    │  (LLM — Chief Supervisor)       │
                    │  → strategy_proposal            │
                    └────────────────┬─────────────────┘
                                     ▼
                    ┌───────────────────────────────┐
                    │  Risk Manager Agent (LLM+rule)  │
                    │  → approved / rejected / adjusted│
                    └────────────────┬─────────────────┘
                                     ▼
                    ┌───────────────────────────────┐
                    │  Execution Agent                │
                    │  (alpaca-py, non-LLM)           │
                    │  → place_option_order()          │
                    └────────────────┬─────────────────┘
                                     ▼
                          Alpaca Trading API
                          (Paper Trading)
                                     │
                                     ▼
                    ┌───────────────────────────────┐
                    │  Position Monitor                │
                    │  (polling, non-LLM)              │
                    └───────────────────────────────┘
```

### Ringkasan Peran

| Agent | Tipe | Lapor ke | Domain | Input Utama |
|---|---|---|---|---|
| Quant Engine | Non-LLM (Python) | Semua sub-agent LLM | Perhitungan statistik options & underlying | Alpaca Market Data API |
| UnderlyingTrendAgent | LLM | Technical Manager | Arah & momentum harga underlying | quant_report (trend z-score, price data) |
| VolatilityAgent | LLM | Technical Manager | Interpretasi IV Rank, HV/IV spread, skew | quant_report (volatility metrics) |
| NewsEarningsAgent | LLM | Context Manager | Event risk, sentimen berita, proximity earnings | Alpaca News API |
| Technical Manager | LLM | Strategy Decision Agent | Kompilasi Trend + Volatility report | 2 sub-agent report |
| Context Manager | LLM | Strategy Decision Agent | Kompilasi News/Event report | 1 sub-agent report |
| Strategy Decision Agent | LLM (Chief) | Risk Manager Agent | Pilih satu `candidate_id` dari Quant whitelist atau WAIT | technical_report + context_report + whitelist |
| Risk Manager Agent | LLM + rule-based check | Execution Agent | Validasi risk sebelum eksekusi | strategy_proposal + account info |
| Execution Agent | Non-LLM (alpaca-py) | Position Monitor | Submit order ke Alpaca | approved strategy_proposal |
| Position Monitor | Non-LLM (polling) | — | Tracking status posisi & order | Alpaca Trading API |

---

## 3. Format Report Standar

### Opsi A — Report Ringkas (antar-LLM: sub-agent → manager → chief)

Digunakan untuk komunikasi antar-LLM agar hemat context window.

```json
{
  "agent": "VolatilityAgent",
  "timestamp": "2026-08-25T14:32:00Z",
  "symbol": "AAPL",
  "bias": "SELL_PREMIUM",
  "confidence": 0.78,
  "volatility_regime": "HIGH_IV",
  "key_points": [
    "IV Rank 82 — premium options relatif mahal secara historis",
    "HV 30D jauh di bawah IV saat ini, spread +6.2 poin",
    "Skew put condong tinggi, indikasi permintaan hedge meningkat"
  ],
  "risk_flags": [
    "Earnings AAPL dalam 5 hari — IV crush risk pasca rilis"
  ]
}
```

### Opsi B — Report Detail (Quant Engine → semua sub-agent)

Berisi semua nilai numerik mentah, dipakai sebagai input analisis sub-agent.

```json
{
  "source": "QuantEngine",
  "timestamp": "2026-08-25T14:30:00Z",
  "symbol": "AAPL",
  "underlying_price": 227.35,
  "volatility": {
    "iv_atm_30d": 0.284,
    "iv_rank_1y": 82.3,
    "iv_percentile_1y": 79.1,
    "hv_30d_realized": 0.221,
    "hv_iv_spread": 0.063,
    "skew_put_call_25delta": 0.041
  },
  "expected_move": {
    "atm_straddle_price": 8.42,
    "expected_move_pct": 3.71,
    "expiry_used": "2026-09-19"
  },
  "trend": {
    "sma_20": 223.10,
    "sma_50": 219.85,
    "z_score_20d": 0.62,
    "direction": "MILD_UPTREND"
  },
  "option_chain_summary": {
    "expiries_available": ["2026-09-05", "2026-09-12", "2026-09-19", "2026-10-17"],
    "atm_call_delta": 0.51,
    "atm_put_delta": -0.49,
    "avg_open_interest_atm": 4820,
    "avg_bid_ask_spread_pct": 0.038
  }
}
```

### Format Output — strategy_proposal (Strategy Decision Agent)

```json
{
  "agent": "StrategyDecisionAgent",
  "timestamp": "2026-08-25T14:35:00Z",
  "symbol": "AAPL",
  "strategy_type": "LONG_CALL",
  "candidate_id": "AAPL260919C00230000",
  "rationale": "Momentum bullish dan candidate Quant memiliki probability lower bound serta expected value yang lolos gate",
  "legs": [
    {"action": "BUY", "type": "CALL", "symbol": "AAPL260919C00230000", "qty": 1}
  ],
  "estimated_debit": 2.15,
  "max_loss": 215.0,
  "max_profit": null,
  "probability_of_profit": 0.58,
  "confidence": 0.74
}
```

### Format Output — risk_decision (Risk Manager Agent)

```json
{
  "agent": "RiskManagerAgent",
  "timestamp": "2026-08-25T14:36:00Z",
  "symbol": "AAPL",
  "decision": "APPROVED",
  "checks": {
    "max_loss_within_limit": true,
    "exposure_within_limit": true,
    "buying_power_sufficient": true,
    "liquidity_acceptable": true,
    "dte_within_range": true
  },
  "adjusted_qty": 1,
  "notes": "Max loss $315 = 1.2% dari buying power, dalam batas 3%"
}
```

---

## 4. Quant Engine (Non-LLM)

### Identitas

| Parameter | Nilai |
|---|---|
| Tipe | Python murni (Pandas/Numpy), tanpa LLM |
| Menerima dari | Alpaca Market Data API (`StockHistoricalDataClient`, `OptionHistoricalDataClient`) |
| Mengirim ke | UnderlyingTrendAgent, VolatilityAgent |
| Loop | Dijalankan di awal tiap siklus, sebelum semua LLM agent |

### Tugas Utama

Menghitung seluruh metrik statistik yang dibutuhkan analisis options, murni dari formula matematis — tidak ada interpretasi. Ini fondasi presisi yang membuat LLM tidak perlu "membaca angka mentah" secara naratif (yang rawan salah), melainkan menerima kesimpulan numerik yang sudah benar secara matematis.

### Perhitungan Detail

**IV Rank & IV Percentile**
```
IV Rank = (current_IV - min_IV_252d) / (max_IV_252d - min_IV_252d) * 100
IV Percentile = % hari dalam 252 hari terakhir dengan IV < current_IV
```
Sumber `current_IV`: rata-rata IV kontrak ATM dari `get_option_snapshot`. Historical IV range: dari `get_option_bars` (jika tersedia) atau proxy dari HV historis jika data IV historis terbatas.

**HV (Historical/Realized Volatility)**
```
HV_30d = std(log_returns_30d) * sqrt(252)
```
Dari `get_stock_bars` daily, 30 hari terakhir.

**Expected Move**
```
Expected Move (%) = (ATM_call_price + ATM_put_price) / underlying_price * 100
```
Menggunakan harga straddle ATM pada expiry terdekat yang relevan (dari `get_option_chain`).

**Skew**
```
Skew = IV_put_25delta - IV_call_25delta
```
Nilai positif besar → permintaan proteksi downside lebih tinggi (skew "fear" khas equity).

**Forward-target probability**
```
P(target) = successful forward returns / valid historical samples
```
Probability dilaporkan bersama sample size dan one-sided Wilson lower bound.
Underlying-only probability adalah gate diagnostik sampai tervalidasi terhadap
historical option P/L; bukan jaminan profit.

**Trend Z-score**
```
Z = (price - SMA_20) / std_20
```
Menghasilkan sinyal arah tanpa interpretasi naratif; label dan kontrak executable
tetap dibatasi oleh Quant gate.

### Output

Format Opsi B (lihat §3) — dikirim ke UnderlyingTrendAgent dan VolatilityAgent.

---

## 5. UnderlyingTrendAgent

### Identitas

| Parameter | Nilai |
|---|---|
| Model | Featherless light tier (`zai-org/GLM-5.2`) |
| Lapor ke | Technical Manager |
| Domain | Arah & momentum harga underlying |
| Input | quant_report (trend section) + harga historis ringkas |

### Tugas Utama

Menyimpulkan bias arah underlying (bullish/bearish/netral) dan kekuatan trend berdasarkan trend z-score, posisi harga relatif terhadap SMA, dan pola harga terkini — **bukan sekadar mengulang angka**, tapi memberi kesimpulan kontekstual (mis. "trend lemah, kemungkinan konsolidasi sebelum breakout").

### Output (Opsi A)

```json
{
  "agent": "UnderlyingTrendAgent",
  "bias": "BULLISH | BEARISH | NEUTRAL",
  "trend_strength": "STRONG | MODERATE | WEAK",
  "confidence": 0.0,
  "key_points": ["..."],
  "risk_flags": ["..."]
}
```

---

## 6. VolatilityAgent

### Identitas

| Parameter | Nilai |
|---|---|
| Model | Featherless light tier (`zai-org/GLM-5.2`) |
| Lapor ke | Technical Manager |
| Domain | Interpretasi IV Rank, HV/IV spread, skew, expected move |
| Input | quant_report (volatility & expected_move section) |

### Tugas Utama

Menentukan **volatility regime** sebagai konteks risiko untuk kontrak long call/put.
VolatilityAgent tidak dapat mengaktifkan short premium, spread, atau kontrak di
luar whitelist.

### Output (Opsi A)

```json
{
  "agent": "VolatilityAgent",
  "volatility_regime": "HIGH_IV | LOW_IV | NEUTRAL",
  "premium_bias": "BUY_PREMIUM | NEUTRAL",
  "confidence": 0.0,
  "key_points": ["..."],
  "risk_flags": ["..."]
}
```

---

## 7. NewsEarningsAgent

### Identitas

| Parameter | Nilai |
|---|---|
| Model | Featherless light tier (`zai-org/GLM-5.2`) |
| Lapor ke | Context Manager |
| Domain | Berita terkini, proximity earnings, event risk |
| Input | Alpaca News API (`get_news`), kalender earnings (jika tersedia) |

### Tugas Utama

Mengidentifikasi event risk yang bisa mempengaruhi pricing options secara signifikan — terutama **earnings date** (karena IV crush pasca-earnings adalah risiko besar untuk pembeli option) dan berita signifikan lain (guidance, litigasi, macro event terkait sektor).

### Output (Opsi A)

```json
{
  "agent": "NewsEarningsAgent",
  "event_risk": "CRITICAL | HIGH | MEDIUM | LOW",
  "earnings_proximity_days": 5,
  "sentiment": "POSITIVE | NEGATIVE | NEUTRAL",
  "confidence": 0.0,
  "key_points": ["..."],
  "risk_flags": ["Earnings dalam 5 hari — waspada IV crush jika strategi long premium"]
}
```

---

## 8. Technical Manager

### Identitas

| Parameter | Nilai |
|---|---|
| Model | Featherless heavy tier (`zai-org/GLM-5.3-Flash`) |
| Menerima dari | UnderlyingTrendAgent, VolatilityAgent |
| Mengirim ke | Strategy Decision Agent |

### Tugas Utama

Mengkompilasi 2 report sub-agent teknikal menjadi satu `technical_report` ringkas, mengidentifikasi apakah kedua sinyal (trend & volatility) **saling mendukung atau bertentangan**, dan memberi kesimpulan gabungan.

### Output

```json
{
  "manager": "TechnicalManager",
  "symbol": "AAPL",
  "overall_bias": "BULLISH_LOW_CONVICTION",
  "volatility_regime": "HIGH_IV",
  "alignment": "PARTIAL",
  "summary": "Trend dan volatility context mendukung evaluasi long premium; kontrak tetap harus lolos Quant whitelist",
  "confidence_score": 0.71,
  "critical_risks": ["Trend belum konfirmasi breakout"]
}
```

---

## 9. Context Manager

### Identitas

| Parameter | Nilai |
|---|---|
| Model | Featherless light tier (`zai-org/GLM-5.2`) |
| Menerima dari | NewsEarningsAgent |
| Mengirim ke | Strategy Decision Agent |

### Tugas Utama

Mengkompilasi report event/berita menjadi ringkasan konteks yang relevan untuk keputusan strategi — terutama highlight jika ada **earnings dekat** yang harus mempengaruhi pemilihan expiry atau tipe strategi (mis. hindari long premium jika earnings sangat dekat karena IV crush).

### Output

```json
{
  "manager": "ContextManager",
  "symbol": "AAPL",
  "overall_event_risk": "MEDIUM",
  "summary": "Earnings dalam 5 hari, sentimen berita netral-positif, tidak ada red flag signifikan",
  "confidence_score": 0.80,
  "earnings_warning": true
}
```

---

## 10. Strategy Decision Agent — Chief Supervisor

### Identitas

| Parameter | Nilai |
|---|---|
| Model | Featherless heavy tier (`zai-org/GLM-5.3-Flash`) |
| Menerima dari | Quant Engine, Technical Manager, Context Manager |
| Mengirim ke | Risk Manager Agent |

### Tugas Utama

**Ini adalah agent paling kritis dalam sistem** — memberi konteks dan memilih
satu kontrak dari whitelist Quant berdasarkan sinyal trend, volatility, dan event
risk. LLM tidak boleh membuat struktur atau mengubah detail kontrak.

### Ruang Keputusan

| Kondisi Umum | Keputusan |
|---|---|
| Quant bullish dan candidate lolos semua gate | Pilih satu `LONG_CALL` candidate |
| Quant bearish dan candidate lolos semua gate | Pilih satu `LONG_PUT` candidate |
| Data, probability, liquidity, news, atau candidate tidak valid | `WAIT` |
| Event risk `CRITICAL` | `WAIT` |

Agent **wajib** menyertakan `candidate_id` dan `rationale`; Python tetap
memvalidasi pilihan tersebut sebelum risk check dan execution.

### Prompt Template (ringkasan)

```
SYSTEM:
Kamu adalah Strategy Decision Agent untuk sistem trading options otonom.
Kamu menerima Quant report, exact candidate whitelist, technical_report, dan
context_report yang sudah dikompilasi. Tugasmu: pilih satu candidate_id dari
whitelist atau WAIT. Kamu tidak boleh membuat atau mengubah symbol, strike,
expiry, option type, action, quantity, atau risk budget.
Kamu HARUS menjelaskan rationale keputusanmu.
Kamu TIDAK mengeksekusi order — hanya mengusulkan strategi.
Output JSON valid sesuai schema strategy_proposal.

USER:
Technical Report: {technical_report}
Context Report: {context_report}
Quant Candidate Whitelist: {candidate_whitelist}
Account Buying Power: {buying_power}
```

### Output

Lihat format `strategy_proposal` di §3.

---

## 11. Risk Manager Agent

### Identitas

| Parameter | Nilai |
|---|---|
| Tipe | Hybrid — rule-based check (Python) + LLM untuk judgment kualitatif |
| Menerima dari | Strategy Decision Agent |
| Mengirim ke | Execution Agent |

### Tugas Utama

Gate wajib sebelum eksekusi. Bagian rule-based (deterministik, tidak boleh di-bypass LLM) mengecek angka keras (max loss, buying power, exposure, liquidity, DTE — lihat `PRD.md` §7). Bagian LLM memberi judgment tambahan jika ada ambiguitas (mis. apakah rationale strategi cukup kuat, apakah ada risk yang terlewat dari sub-agent).

### Alur Keputusan

```
1. Jalankan rule-based checks (deterministik):
   - max_loss_within_limit?
   - exposure_within_limit?
   - buying_power_sufficient?
   - liquidity_acceptable?
   - dte_within_range?
2. Jika SEMUA rule pass → LLM review rationale (sanity check kualitatif)
   → APPROVED (dengan qty tetap 1)
3. Jika ADA rule gagal → REJECTED otomatis, tidak perlu LLM
   (rule-based check tidak boleh di-override oleh LLM demi keamanan)
```

### Output

Lihat format `risk_decision` di §3.

---

## 12. Execution Agent

### Identitas

| Parameter | Nilai |
|---|---|
| Tipe | Non-LLM — deterministik, memanggil `alpaca-py` langsung |
| Menerima dari | Risk Manager Agent (hanya jika APPROVED) |
| Mengirim ke | Position Monitor |

### Tugas Utama

Menerjemahkan `strategy_proposal` yang sudah approved menjadi satu order long
call/put via Alpaca Trading API. **Tidak ada reasoning di sini** — murni
eksekusi teknis dari keputusan yang sudah divalidasi.

### Logic

```python
if risk_decision.decision == "APPROVED":
    # The Python gate has already verified exactly one BUY leg.
    order = trading_client.submit_order(
        order_data=OptionOrderRequest(...)
    )
    log_execution(order)
else:
    log_rejection(risk_decision)
    # tidak ada order dikirim
```

Selalu memverifikasi `ALPACA_PAPER_TRADE` sebelum submit — safety check tambahan di level kode, terlepas dari konfigurasi environment.

---

## 13. Position Monitor

### Identitas

| Parameter | Nilai |
|---|---|
| Tipe | Non-LLM — polling periodik |
| Sumber | `get_all_positions`, `get_orders`, `get_order_by_id` |

### Tugas Utama

Melacak status posisi & order yang sudah dieksekusi, menyimpan history untuk keperluan reporting dan (di fase berikutnya) feedback loop ke agent untuk pembelajaran kontekstual.

---

## 14. Data Flow Lengkap (End-to-End)

```
SATU SIKLUS PENUH (dipicu manual atau tiap CYCLE_INTERVAL_MIN):

0. Pre-check
   get_clock() → skip siklus jika market tutup

1. Quant Engine (Python, ~1-3 detik)
   Fetch data underlying + option chain dari Alpaca
   Hitung IV Rank, HV/IV spread, expected move, skew, PoP, trend z-score
   → quant_report (Opsi B)

2. Sub-Agents (LLM, sequential loading)
   [Load model] → UnderlyingTrendAgent(quant_report) → trend_report [Unload]
   [Load model] → VolatilityAgent(quant_report) → volatility_report [Unload]
   [Load model] → NewsEarningsAgent(news_data) → news_report [Unload]

3. Managers (LLM)
   [Load model] → TechnicalManager(trend_report, volatility_report)
                → technical_report [Unload]
   [Load model] → ContextManager(news_report)
                → context_report [Unload]

4. Strategy Decision Agent (LLM — Chief)
   [Load model] → StrategyDecisionAgent(quant_report, candidate_whitelist,
                  technical_report, context_report, buying_power)
                → strategy_proposal [Unload]

5. Risk Manager Agent (rule-based + LLM)
   Jalankan rule checks deterministik
   Jika semua pass → [Load model] LLM sanity check → risk_decision [Unload]
   Jika ada gagal → risk_decision = REJECTED (tanpa LLM call)

6. Execution Agent (non-LLM)
   Jika APPROVED → place_option_order() via alpaca-py (Paper Trading)
   Jika REJECTED → log alasan, tidak ada order

7. Position Monitor (non-LLM)
   Update status posisi & order aktif

8. Simpan cycle_report.json
   Berisi seluruh report dari langkah 1-6 sebagai reasoning trail lengkap
```

---

## 15. Contoh Skenario Lengkap

**Input:** Symbol AAPL, harga $227.35

**Quant Engine** menghasilkan: IV Rank 82.3 (tinggi), HV/IV spread +6.3 poin, trend z-score +0.62 (mild uptrend), expected move 3.71%, earnings dalam 5 hari.

**UnderlyingTrendAgent** menyimpulkan: bias `BULLISH`, strength `WEAK` — trend positif tapi belum breakout kuat.

**VolatilityAgent** menyimpulkan kondisi volatilitas dan risiko event sebagai
konteks; ia tidak dapat mengubah Quant whitelist atau mengaktifkan short premium.

**NewsEarningsAgent** menyimpulkan: event_risk `MEDIUM`, earnings_proximity_days 5.
Jika confidence rendah atau headline tidak tersedia, pipeline fail-closed;
`CRITICAL` hanya boleh digunakan untuk berita bersumber, fresh, dan memiliki
confidence tinggi.

**Technical Manager** compile: overall_bias `BULLISH_LOW_CONVICTION`, alignment `PARTIAL` (trend lemah tapi searah dengan volatility signal untuk strategi credit).

**Context Manager** compile: event_risk `MEDIUM`, earnings_warning `true`.

**Strategy Decision Agent** memilih satu `candidate_id` dari whitelist Quant
atau `WAIT`. Ia tidak boleh membuat spread, short leg, atau kontrak yang tidak
ada di whitelist.

**Risk Manager Agent**: cek max loss ($315) = 1.2% buying power (dalam limit 3%), exposure AAPL masih di bawah limit, liquidity OK (spread 3.8%), DTE 25 hari (dalam range 7-45) → **APPROVED**.

**Execution Agent**: submit satu long call/put via `place_option_order` — paper trading.

**Position Monitor**: posisi tercatat, status `OPEN`, akan dipantau siklus berikutnya.

---

## 16. Error Handling & Fallback

| Skenario | Penanganan |
|---|---|
| LLM output bukan JSON valid | Retry 1x dengan prompt diperketat; jika tetap gagal → fallback ke default netral (`WAIT`/`NEUTRAL`) untuk agent tsb, lanjut ke agent berikutnya |
| Option chain kosong/tidak likuid untuk symbol | Skip symbol tsb di siklus ini, log alasan |
| Featherless timeout | Fallback ke keputusan konservatif; catat di cycle_report sebagai `degraded_cycle: true` |
| Risk Manager reject | Tidak ada order, log lengkap rationale reject untuk audit |
| Alpaca API error (rate limit/auth) | Retry dengan exponential backoff; jika gagal total → skip siklus, alert log |
| Market tertutup | Siklus tidak dijalankan sama sekali (dicek di awal via `get_clock`) |

---

## 17. Referensi

- Alpaca AI Trading Agents Hackathon: https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
- Alpaca Skills: https://github.com/alpacahq/alpaca-skills
- Getting Started with Trading API: https://docs.alpaca.markets/us/docs/getting-started-with-trading-api
- Getting Started with Market Data API: https://docs.alpaca.markets/us/docs/getting-started-with-alpaca-market-data
- Alpaca Trade API (JS SDK): https://github.com/alpacahq/alpaca-trade-api-js
- Alpaca-py (Python SDK): https://github.com/alpacahq/alpaca-py
- Alpaca-py Options Trading Example: https://github.com/alpacahq/alpaca-py/blob/master/examples/options/options-trading-basic.ipynb
- Alpaca CLI: https://github.com/alpacahq/cli
- Alpaca CLI Docs: https://docs.alpaca.markets/us/docs/alpacas-cli
- Alpaca MCP Server: https://docs.alpaca.markets/us/docs/alpaca-mcp-server
- Building a Multi-Agent AI Trading System on Alpaca: https://alpaca.markets/learn/building-a-multi-agent-ai-trading-system-on-alpaca
- SDKs and Tools: https://docs.alpaca.markets/us/docs/sdks-and-tools
- Options Trading Overview: https://docs.alpaca.markets/us/docs/options-trading-overview
- Ollama: https://ollama.ai

---

*AGENTS.md v1.0 | Autonomous Multi-Agent Options Trading System | Alpaca AI Trading Agents Hackathon 2026*
