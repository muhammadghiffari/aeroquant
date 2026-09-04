# Product Requirements Document
# Autonomous Multi-Agent Options Trading System — v1.0
### Hierarchical Quant + LLM Swarm untuk US Equity Options di Alpaca

> **Status:** Draft — Hackathon Build
> **Versi:** v1.0
> **Event:** Alpaca AI Trading Agents Hackathon (lablab.ai), 28 Agustus – 4 September 2026
> **Instrumen:** US Equity Options (single-leg long call/put), via Alpaca Trading API
> **Mode:** Paper Trading only — live trading is disabled in v1.0
> **LLM:** Featherless OpenAI-compatible API with light/heavy GLM tiers
> **Turunan dari:** Sistem sebelumnya "AI Crypto Futures Trading Bot v4.3" (BTC/USDT Perpetual) — arsitektur hierarchical swarm dipertahankan, seluruh domain logic (ML crypto, leverage, indikator perp) diganti total untuk US equity options.

---

## Daftar Isi

1. [Ringkasan Eksekutif](#1-ringkasan-eksekutif)
2. [Tujuan & Ruang Lingkup](#2-tujuan--ruang-lingkup)
3. [Latar Belakang & Perbandingan dengan Sistem Sebelumnya](#3-latar-belakang--perbandingan-dengan-sistem-sebelumnya)
4. [Arsitektur Sistem](#4-arsitektur-sistem)
5. [Logic Inti — Quant Layer + LLM Reasoning Layer](#5-logic-inti--quant-layer--llm-reasoning-layer)
6. [LLM Stack & Manajemen Resource](#6-llm-stack--manajemen-resource)
7. [Risk Management untuk Options](#7-risk-management-untuk-options)
8. [Data Sources & Alpaca API Mapping](#8-data-sources--alpaca-api-mapping)
9. [Struktur Kode & Direktori](#9-struktur-kode--direktori)
10. [Konfigurasi Parameter](#10-konfigurasi-parameter)
11. [Cara Menjalankan](#11-cara-menjalankan)
12. [Risiko & Mitigasi](#12-risiko--mitigasi)
13. [Rencana Pengembangan](#13-rencana-pengembangan)
14. [Kriteria Sukses Hackathon](#14-kriteria-sukses-hackathon)
15. [Glosarium](#15-glosarium)
16. [Referensi](#16-referensi)

---

## 1. Ringkasan Eksekutif

Proyek ini adalah **sistem trading options otonom berbasis multi-agent** untuk pasar **US Equity Options**, dibangun di atas **Alpaca Trading API, Market Data API, dan (opsional) MCP Server**. Sistem menggabungkan dua lapisan pengambilan keputusan:

1. **Quant Layer** — perhitungan statistik murni (non-LLM) atas data harga underlying dan option chain: IV Rank, Expected Move, HV/IV spread, skew, Probability of Profit, dan sinyal trend kuantitatif. Layer ini menghasilkan angka terukur, bukan interpretasi naratif.

2. **LLM Reasoning Layer** — beberapa agent LLM via Featherless yang menerima output Quant Layer, mensintesis dengan konteks pasar (berita, earnings, kondisi makro), dan **secara otonom memutuskan** proposal strategi options yang akan dieksekusi setelah hard risk gate.

Filosofi utama: **Quant menyediakan data yang presisi dan defendable secara matematis; LLM menyediakan penalaran kontekstual dan pengambilan keputusan strategi.** Tidak ada satupun layer yang menggantikan yang lain — keduanya bekerja berurutan dalam satu pipeline.

Berbeda dengan sistem crypto futures sebelumnya yang mengandalkan scalping berkecepatan tinggi, sistem ini memakai regular options entry cycle dan posisi monitor terpisah. Entry hanya mengambil satu kontrak long yang lolos Quant whitelist; BTC hanya shadow telemetry dan tidak dapat menghasilkan trade.

---

## 2. Tujuan & Ruang Lingkup

### 2.1 Tujuan Proyek

| No | Tujuan | Metrik Keberhasilan |
|----|--------|----------------------|
| 1 | Agent mampu menganalisis underlying + volatility options secara otonom | Quant Layer menghasilkan report lengkap tanpa intervensi manual |
| 2 | Agent memilih kontrak options yang sesuai kondisi pasar | Strategy Decision Agent hanya memilih `candidate_id` dari Quant whitelist atau `WAIT` |
| 3 | Sistem melakukan risk-check sebelum eksekusi | Tidak ada order lolos tanpa validasi Risk Manager Agent |
| 4 | Eksekusi order berjalan aman via paper trading | 100% transaksi via `ALPACA_PAPER_TRADE=true` selama development |
| 5 | Reasoning trail transparan untuk demo/juri | Setiap keputusan agent tersimpan sebagai structured JSON report |
| 6 | LLM remote Featherless berjalan stabil dengan dua model tier | Single-shot schema calls, timeout + fallback |

### 2.2 Ruang Lingkup v1.0

- **Instrumen:** US Equity Options (initial focus: high-liquidity underlyings — SPY, QQQ, AAPL, NVDA, TSLA, MSFT)
- **Tipe order:** Single-leg long call/put via `place_option_order`; multi-leg dan short-premium strategies tidak executable di v1.0
- **Data:** Real-time & historical dari Alpaca Market Data API (stock bars, option chain, option snapshot dengan Greeks, news)
- **LLM:** Featherless OpenAI-compatible API; agent light memakai GLM-5.2 dan agent heavy memakai GLM-5.3-Flash melalui konfigurasi environment
- **Eksekusi:** Alpaca Trading API (`alpaca-py`) — Paper Trading sebagai default
- **Orkestrasi:** Sequential Quant-first entry hierarchy dan deterministic position monitor; tidak ada scalp entry cycle
- **Interaksi:** Bisa dipicu manual (on-demand analysis command) atau berjalan periodik (mis. tiap N menit selama jam bursa)

### 2.3 Di Luar Ruang Lingkup v1.0

- Live trading dengan uang riil (ditunda sampai paper trading terbukti stabil)
- Custom-trained ML model (XGBoost/LSTM/dsb.) untuk price prediction — digantikan quant statistik + LLM reasoning demi kecepatan development hackathon
- Multi-asset class selain equity options (tidak termasuk crypto options atau futures)
- Dashboard visual penuh (fase berikutnya — cukup CLI/log terstruktur + laporan JSON untuk v1.0)
- Backtesting options profitability tanpa historical bid/ask point-in-time, slippage, fee, dan non-fill data yang memadai

---

## 3. Latar Belakang & Perbandingan dengan Sistem Sebelumnya

Proyek ini adalah **evolusi arsitektur**, bukan port langsung, dari sistem *AI Crypto Futures Trading Bot v4.3* yang sebelumnya dibangun tim untuk BTC/USDT Perpetual Futures di Binance (100x leverage). Tabel berikut merangkum apa yang dipertahankan dan apa yang diganti total, berdasarkan analisis kesesuaian domain:

| Aspek | Sistem Lama (Crypto Futures) | Sistem Baru (Equity Options) | Keputusan |
|---|---|---|---|
| Pola orkestrasi (sub-agent → manager → chief supervisor) | Hierarchical swarm, sequential LLM loading | **Dipertahankan** — pola sama, isi agent diganti | Pertahankan |
| Format report terstruktur (ringkas vs detail) | Opsi A (ringkas antar-LLM) / Opsi B (detail numerik) | **Dipertahankan** | Pertahankan |
| Fast Loop (60s) + Provisional ML Executor | Wajib karena leverage 100x & scalping crypto 24/7 | **Dihapus** — options tidak scalping per-detik, market jam terbatas | Buang |
| ML Engine (XGBoost/LSTM/CNN/Transformer + training pipeline) | Price prediction dari historical candle sejak 2019 | **Dihapus** — diganti Quant Layer statistik + LLM reasoning | Buang, ganti pendekatan |
| Reconciliation Engine (ML vs LLM conflict) | Menangani konflik 2 sumber keputusan berkecepatan beda | **Dihapus** — hanya ada 1 alur keputusan (Quant → LLM) | Buang |
| Leverage 100x, SL 0.3%, Kelly sizing | Risk model untuk leverage ekstrem | **Dihapus** — diganti risk model options (max loss per struktur) | Buang, ganti pendekatan |
| Exchange integration (ccxt.binanceusdm) | Binance USDT-M Futures Testnet | **Diganti** Alpaca Trading API (`alpaca-py`) | Ganti total |
| Sentiment source (RSS crypto + Fear & Greed) | CoinTelegraph, Decrypt, FNG Index | **Diganti** Alpaca News API + earnings calendar | Ganti total |
| Indikator teknikal (EMA/RSI/funding rate/OI/CVD) | Spesifik crypto perp (funding, OI tidak eksis di options) | **Diganti** Quant Layer options-specific (IV Rank, skew, Expected Move, Greeks) | Ganti total |
| Claude API sebagai Entry Assistant (disabled) | Validasi numerik entry/TP/SL | **Diperluas peran** jadi Strategy Validator (opsional) | Adaptasi |

Kesimpulan desain: **arsitektur hierarchical multi-agent dipertahankan sebagai kerangka**, tetapi seluruh konten domain (model, indikator, risk parameter, sumber data, exchange) ditulis ulang dari nol agar sesuai karakteristik pasar options AS.

---

## 4. Arsitektur Sistem

### 4.1 Gambaran Umum

```
INPUT
[ Alpaca Market Data API ]              [ Alpaca News API ]
   Stock bars, Option chain,              News per symbol,
   Option snapshot + Greeks               earnings proximity
         │                                       │
         ▼                                       │
[ Quant Engine (non-LLM, Python) ]                │
   - IV Rank / IV Percentile                      │
   - HV vs IV spread                               │
   - Expected Move (ATM straddle)                  │
   - Skew (put/call IV)                            │
   - Empirical target probability + Wilson lower bound │
   - Trend z-score underlying                       │
         │                                          │
    ┌────┴──────────────────────────────────────────┘
    │
    │  SEQUENTIAL PIPELINE (single-cycle, bukan dual loop)
    │  ─────────────────────────────────────────────────
    │
    │  ┌─ Layer 1: Sub-Agents (LLM, sequential loading) ──┐
    │  │  UnderlyingTrendAgent   (arah & momentum saham)  │
    │  │  VolatilityAgent        (interpretasi IV/HV)     │◄── Quant Engine output
    │  │  NewsEarningsAgent      (event risk, sentimen)   │◄── Alpaca News API
    │  └───────────────┬────────────────────────────────┘
    │                  │
    │  ┌─ Layer 2: Managers (LLM) ─────────────────────┐
    │  │  Technical Manager  → compile Trend+Vol report │
    │  │  Context Manager    → compile News/Event report│
    │  └───────────────┬────────────────────────────────┘
    │                  │
    │  ┌─ Layer 3: Strategy Decision Agent (Chief) ────┐
     │  │  → pilih satu candidate_id dari Quant whitelist │
     │  │    atau WAIT                                  │
     │  │  → output: strategy_proposal                  │
    │  └───────────────┬────────────────────────────────┘
    │                  │
    │  ┌─ Layer 4: Risk Manager Agent ──────────────────┐
    │  │  → cek buying power, max loss, exposure limit  │
    │  │  → approve / reject / adjust sizing             │
    │  └───────────────┬────────────────────────────────┘
    │                  │
    └──► [ Execution Agent ]
              │
              ▼
         Alpaca Trading API (alpaca-py)
         place_option_order() — Paper Trading
              │
              ▼
    [ Position & Portfolio Monitor ]
    (get_all_positions, get_account_info,
     get_orders — polling periodik)
```

### 4.2 Prinsip Desain Kunci

1. **Quant di depan, LLM di belakang.** Quant Engine tidak pernah menunggu LLM — ia jalan duluan sebagai fondasi data. LLM hanya bekerja dengan angka yang sudah terhitung presisi, mengurangi risiko halusinasi numerik dari LLM.
2. **Sequential, bukan paralel.** Semua LLM agent dijalankan satu per satu (load model → run → unload) untuk menghemat VRAM terbatas, konsisten dengan prinsip sistem sebelumnya.
3. **Sequential hierarchy per keputusan.** Entry menjalankan Quant → Sub-agent → Manager → Chief → Risk → Execution; monitor posisi berjalan terpisah dan deterministik untuk TP/SL, berita kritis, reversal, dan expiry safety.
4. **Human-in-the-loop opsional untuk live trading.** Selama paper trading, eksekusi otomatis penuh. Untuk live trading, order dari Execution Agent bisa di-gate dengan konfirmasi manual sebagai safety net tambahan.
5. **Reasoning trail wajib disimpan.** Setiap laporan agent (Quant, sub-agent, manager, chief, risk) disimpan sebagai JSON terstruktur per siklus, untuk transparansi dan keperluan demo.

---

## 5. Logic Inti — Quant Layer + LLM Reasoning Layer

### 5.1 Quant Engine (Non-LLM)

Dihitung murni dengan Python/Numpy/Pandas dari data Alpaca, **tanpa panggilan LLM**:

| Metrik | Formula / Sumber | Fungsi |
|---|---|---|
| **IV Rank** | `(current_IV - IV_low_1y) / (IV_high_1y - IV_low_1y)` | Menentukan apakah premium options "mahal" atau "murah" secara historis |
| **IV Percentile** | Persentase hari dalam 1 tahun terakhir dengan IV di bawah level saat ini | Alternatif IV Rank, lebih tahan outlier |
| **HV vs IV Spread** | Historical Volatility (realized, dari `get_stock_bars`) vs Implied Volatility (dari `get_option_snapshot`) | Sinyal apakah pasar "overpricing" atau "underpricing" pergerakan riil |
| **Expected Move** | ATM straddle price / underlying price | Estimasi pergerakan yang di-price-in pasar sampai expiry |
| **Skew** | IV put OTM vs IV call OTM pada delta setara | Indikasi sentimen crowd (put skew tinggi = permintaan hedge/fear) |
| **Target probability** | Empirical forward-return samples + Wilson lower bound | Hard gate konservatif; bukan jaminan profit |
| **Trend Z-score** | `(price - SMA_N) / std_N` pada underlying | Sinyal arah & kekuatan trend tanpa interpretasi naratif |

Output Quant Engine adalah JSON numerik (format "Opsi B" — lihat AGENTS.md §2) yang menjadi **input wajib** untuk Volatility Agent dan Underlying Trend Agent.

### 5.2 LLM Reasoning Layer

LLM tidak menghitung ulang angka — ia **menginterpretasikan** angka dari Quant Engine dan menggabungkannya dengan konteks kualitatif (berita, earnings, kondisi makro) untuk memilih satu candidate dari whitelist atau `WAIT`. Detail lengkap tiap agent (peran, prompt, input/output) dijelaskan di `AGENTS.md`.

Contoh alur singkat pengambilan keputusan:

```
Probability lower bound = 0.61 + Trend Z-score = +0.8 (bullish)
   → VolatilityAgent: "ATM premium dan spread masih acceptable untuk long option"
   → UnderlyingTrendAgent: "Bias bullish dengan momentum terkonfirmasi"
   → Strategy Decision Agent: "Pilih candidate_id long call dari Quant whitelist"
```

---

## 6. LLM Stack & Manajemen Resource

- **Runtime:** Featherless OpenAI-compatible API (`FEATHERLESS_BASE_URL`)
- **Model tiers:** agent light memakai `FEATHERLESS_LIGHT_MODEL` (`zai-org/GLM-5.2`); chief, technical manager, dan lesson distiller memakai `FEATHERLESS_HEAVY_MODEL` (`zai-org/GLM-5.3-Flash`)
- **Single-shot calls:** setiap agent mengirim schema-bound request stateless; tidak ada conversation history yang direplay.
- **Timeout per LLM call:** disarankan 60–120 detik, dengan fallback (skip agent / gunakan default netral) jika timeout.
- **Format output:** seluruh agent LLM **wajib** mengeluarkan JSON valid sesuai schema (lihat `AGENTS.md` §2) — retry 1x jika parsing gagal, fallback ke keputusan konservatif (WAIT/no-trade) jika retry tetap gagal.

---

## 7. Risk Management untuk Options

Berbeda total dari risk model leverage crypto. Untuk options, risiko didefinisikan oleh **struktur strategi itu sendiri**:

| Strategi executable | Max Loss | Max Profit | Catatan Risk |
|---|---|---|---|
| Long Call / Put | Premium dibayar | Unlimited (call) / Strike-Premium (put) | Risk terbatas ke premium, tapi time decay (theta) melawan posisi |
| `WAIT` | $0 | $0 | Dipilih jika Quant/news/risk gate tidak lolos |

### Risk Manager Agent — Gate Wajib Sebelum Eksekusi

Setiap `strategy_proposal` dari Strategy Decision Agent **wajib** melewati Risk Manager Agent yang mengecek:

1. **Max loss vs account size** — max loss per posisi tidak melebihi konfigurasi paper risk budget
2. **Portfolio exposure** — jumlah eksposur terhadap underlying yang sama tidak melebihi limit
3. **Buying power check** — cukup buying power untuk premium long option
4. **Liquidity check** — bid-ask spread option tidak terlalu lebar (proxy: spread % dari mid price) untuk menghindari slippage besar
5. **Days to Expiry (DTE) check** — candidate harus berada pada window Quant `7–21` hari

Jika salah satu gagal → Risk Manager **reject**, tidak pernah membiarkan Execution Agent submit order tanpa approval eksplisit. Quantity executable selalu `1`.

---

## 8. Data Sources & Alpaca API Mapping

| Kebutuhan Data | Endpoint / Tool Alpaca | SDK Reference |
|---|---|---|
| Harga & bar underlying | `get_stock_bars`, `get_stock_snapshot` | `alpaca.data.historical.StockHistoricalDataClient` |
| Option chain + Greeks | `get_option_chain`, `get_option_snapshot` | `alpaca.data.historical.OptionHistoricalDataClient` |
| Historical option data (utk HV/IV context) | `get_option_bars`, `get_option_trades` | `OptionHistoricalDataClient` |
| Berita per simbol | `get_news` | `alpaca.data.historical.news.NewsClient` |
| Info akun & buying power | `get_account_info` | `alpaca.trading.client.TradingClient` |
| Posisi terbuka | `get_all_positions`, `get_open_position` | `TradingClient` |
| Submit order options (single-leg) | `place_option_order` | `TradingClient.submit_order` dengan `OptionOrderRequest` |
| Cancel/replace order | `cancel_order_by_id`, `replace_order_by_id` | `TradingClient` |
| Kalender & jam bursa | `get_calendar`, `get_clock` | `TradingClient` |
| Watchlist kandidat underlying | `create_watchlist`, `get_watchlists` | `TradingClient` |

**Dua jalur integrasi yang bisa dipilih saat implementasi:**
1. **Direct SDK (`alpaca-py`)** — kontrol penuh dari kode Python, cocok untuk backend-first development sesuai prioritas tim.
2. **Alpaca MCP Server** — jika ingin agent LLM memanggil tools secara langsung ala "agentic tool calling" (65 tools tersedia, bisa dibatasi lewat `ALPACA_TOOLSETS`). Bisa dipakai sebagai layer tambahan di atas backend inti, terutama untuk demo interaktif.

Kedua jalur bisa dikombinasikan: backend inti pakai `alpaca-py` untuk reliability, MCP Server dipakai sebagai antarmuka opsional untuk chat/demo.

---

## 9. Struktur Kode & Direktori

```
options-agent/
├── PRD.md
├── AGENTS.md
├── .env                          # ALPACA_API_KEY, ALPACA_SECRET_KEY, dll.
├── config.py                     # Semua parameter (lihat §10)
├── main.py                       # Entry point — trigger 1 siklus pipeline
│
├── data_engine/
│   ├── alpaca_client.py          # Wrapper alpaca-py (TradingClient, DataClients)
│   ├── stock_data.py             # Fetch bars/snapshot underlying
│   ├── option_data.py            # Fetch option chain, snapshot, Greeks
│   └── news_data.py              # Fetch news & earnings proximity
│
├── quant_engine/
│   ├── volatility_metrics.py     # IV Rank, IV Percentile, HV/IV spread
│   ├── expected_move.py          # Expected move dari ATM straddle
│   ├── skew.py                   # Put/call skew
│   ├── probability.py            # PoP proxy dari delta
│   └── trend_score.py            # Z-score trend underlying
│
├── agents/
│   ├── base_agent.py             # Base class LLM agent (provider call, JSON parse, retry)
│   ├── underlying_trend_agent.py
│   ├── volatility_agent.py
│   ├── news_earnings_agent.py
│   ├── technical_manager.py
│   ├── context_manager.py
│   ├── strategy_decision_agent.py   # Chief Supervisor
│   └── risk_manager_agent.py
│
├── orchestrator/
│   └── pipeline.py                # Sequential pipeline runner (single-cycle)
│
├── execution/
│   ├── executor.py                # place_option_order via alpaca-py
│   └── position_monitor.py        # Polling posisi & status order
│
├── prompts/
│   └── *.py                       # Prompt template tiap agent
│
├── reports/
│   └── {timestamp}_cycle_report.json   # Reasoning trail per siklus
│
└── tests/
    └── ...
```

---

## 10. Konfigurasi Parameter

```python
# ─── Alpaca ──────────────────────────────────────────────────────────────
ALPACA_API_KEY          = ""
ALPACA_SECRET_KEY       = ""
ALPACA_PAPER_TRADE      = True           # WAJIB True selama development

# ─── Universe ────────────────────────────────────────────────────────────
WATCHLIST_SYMBOLS       = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT"]

# ─── Quant Thresholds ────────────────────────────────────────────────────
IV_RANK_HIGH_THRESHOLD  = 0.70            # di atas ini → condong strategi jual premium
IV_RANK_LOW_THRESHOLD   = 0.30            # di bawah ini → condong strategi beli premium
MIN_LIQUIDITY_OI        = 100             # min open interest kontrak
MAX_BID_ASK_SPREAD_PCT  = 0.10            # max 10% dari mid price

# ─── Risk Management ──────────────────────────────────────────────────────
MAX_LOSS_PCT_PER_TRADE  = 0.03            # max 3% buying power per trade
MAX_EXPOSURE_PER_SYMBOL = 0.15            # max 15% portfolio per underlying
MIN_DTE                 = 7               # hindari expiry < 7 hari (kecuali strategi khusus)
MAX_DTE                 = 45              # hindari expiry terlalu jauh

# ─── LLM (Featherless) ────────────────────────────────────────────────────
LLM_PROVIDER             = "featherless"
FEATHERLESS_LIGHT_MODEL  = "zai-org/GLM-5.2"
FEATHERLESS_HEAVY_MODEL  = "zai-org/GLM-5.3-Flash"
LLM_TIMEOUT_S             = 120
LLM_JSON_RETRY            = 1

# ─── Cycle Timing ──────────────────────────────────────────────────────────
CYCLE_INTERVAL_MIN        = 5             # jalankan siklus tiap 5 menit selama jam bursa
MARKET_HOURS_ONLY         = True          # skip siklus di luar jam bursa (pakai get_clock)
```

---

## 11. Cara Menjalankan

### 11.1 Prerequisites

```bash
# 1. Setup Python environment
python -m venv venv
source venv/bin/activate      # Linux/Mac
# venv\Scripts\activate       # Windows

pip install alpaca-py python-dotenv pandas numpy requests

# 2. Setup .env dengan Featherless API key, paper Alpaca credentials, dan Telegram opsional
# ALPACA_API_KEY=your_paper_api_key
# ALPACA_SECRET_KEY=your_paper_secret_key
```

### 11.2 Jalankan Satu Siklus Analisis (Manual Trigger)

```bash
python main.py --symbol AAPL --once
```

### 11.3 Jalankan Periodik Selama Jam Bursa

```bash
python main.py --loop --interval 5
```

### 11.4 Urutan Startup Internal (main.py)

```
1. Load config & .env
2. Init AlpacaClient (TradingClient + DataClients, Paper mode)
3. Cek get_clock() — skip jika market tutup (jika MARKET_HOURS_ONLY=True)
4. Untuk tiap symbol di watchlist:
   a. Quant Engine hitung semua metrik
   b. Jika Quant gate WAIT → simpan alasan dan lanjut ke symbol berikutnya
   c. Jalankan sub-agent (sequential, single-shot Featherless model call)
   d. Jalankan manager (compile report)
   e. Chief memilih satu `candidate_id` whitelist → strategy_proposal
   f. Jalankan Risk Manager Agent → approve/reject
   g. Jika approved → Execution Agent submit satu long option (paper)
   h. Simpan cycle_report.json (reasoning trail lengkap)
5. Ulangi tiap CYCLE_INTERVAL_MIN jika mode --loop
6. Position monitor terpisah menjalankan TP/SL, critical news, reversal, dan expiry safety
```

---

## 12. Risiko & Mitigasi

| Risiko | Dampak | Probabilitas | Mitigasi |
|---|---|---|---|
| LLM output JSON tidak valid | Pipeline agent gagal | Medium | Retry 1x → fallback ke WAIT/no-trade |
| Option chain data tidak lengkap/stale (butuh subscription data premium) | Keputusan berbasis data usang | Medium | Cek `get_clock`, validasi freshness timestamp, fallback skip symbol |
| Bid-ask spread lebar pada option kurang likuid | Slippage besar saat eksekusi | Medium | Liquidity check di Risk Manager (`MAX_BID_ASK_SPREAD_PCT`) |
| Featherless model lambat/timeout | Siklus analisis molor / gagal | Medium | Timeout + fallback, model light untuk sub-agent |
| Strategy Decision Agent memilih strategi tidak sesuai risk appetite | Potensi loss lebih besar dari ekspektasi | Medium | Risk Manager Agent sebagai gate wajib, max loss per trade dibatasi |
| Order entry tertunda atau tidak terisi | Ledger berbeda dari broker | Low-Medium | Idempotent intent, polling status, dan broker reconciliation |
| Market tutup / holiday tidak terdeteksi | Order gagal submit | Low | Selalu cek `get_calendar`/`get_clock` sebelum siklus jalan |
| Rate limit Alpaca API | Request gagal saat query banyak symbol | Low | Batching request, exponential backoff |

---

## 13. Rencana Pengembangan

### Phase Saat Ini — v1.0 (Hackathon Build)

| # | Item | Prioritas |
|---|------|-----------|
| 1 | Quant Engine (IV Rank, Expected Move, skew, PoP, trend z-score) | 🔴 CRITICAL |
| 2 | Data Engine (Alpaca stock + option + news integration) | 🔴 CRITICAL |
| 3 | Agent pipeline sequential (sub-agent → manager → chief → risk) | 🔴 CRITICAL |
| 4 | Execution Agent (paper trading, single-leg dulu) | 🔴 CRITICAL |
| 5 | Deterministic TP/SL, reversal, dan position reconciliation | 🔴 CRITICAL |
| 6 | Options backtest dengan historical bid/ask | 🟡 HIGH |
| 7 | Reasoning trail JSON per siklus | 🟡 HIGH |
| 8 | CLI sederhana untuk trigger & lihat report | 🟢 MEDIUM |

### Phase Berikutnya — v2.0

| # | Item |
|---|------|
| 1 | Dashboard monitoring (Streamlit) |
| 2 | Integrasi MCP Server untuk interaksi chat langsung |
| 3 | Backtesting engine dengan data historis options |
| 4 | Multi-symbol paralel dengan resource management lebih baik |
| 5 | Live trading gate dengan human confirmation UI |

---

## 14. Kriteria Sukses Hackathon

Berdasarkan tema hackathon ("Code the next generation of algorithmic trading") dan track Options Trading:

1. **Kedalaman integrasi Alpaca** — pemakaian Trading API + Market Data API (options) yang substantif, bukan superficial.
2. **Genuinely agentic** — keputusan strategi benar-benar berasal dari reasoning LLM otonom (bukan if-else murni), didukung data quant yang solid.
3. **Berjalan end-to-end** — dari analisis sampai order paper trading benar-benar tereksekusi, bukan cuma mock/demo statis.
4. **Transparansi reasoning** — reasoning trail per agent bisa ditunjukkan ke juri sebagai bukti proses pengambilan keputusan yang defendable.
5. **Risk-aware** — ada lapisan risk management yang jelas dan spesifik untuk options (bukan copy-paste dari leverage crypto).

---

## 15. Glosarium

| Istilah | Definisi |
|---|---|
| **IV (Implied Volatility)** | Volatilitas yang diimplikasikan oleh harga option saat ini, hasil reverse-engineering dari model pricing |
| **IV Rank** | Posisi IV saat ini relatif terhadap range IV 1 tahun terakhir (0–100%) |
| **HV (Historical/Realized Volatility)** | Volatilitas aktual underlying berdasarkan pergerakan harga historis |
| **Expected Move** | Estimasi pergerakan harga underlying sampai expiry, di-derive dari harga ATM straddle |
| **Skew** | Perbedaan IV antara option OTM put dan call pada delta setara — indikator sentimen |
| **Greeks** | Delta, gamma, theta, vega, rho — sensitivitas harga option terhadap berbagai faktor |
| **DTE (Days to Expiry)** | Jumlah hari sampai option jatuh tempo |
| **Vertical Spread** | Strategi 2-leg dengan strike berbeda, expiry sama (debit atau credit) |
| **Iron Condor** | Strategi 4-leg netral volatilitas, menjual spread di kedua sisi (call & put) |
| **Probability of Profit (PoP)** | Estimasi probabilitas strategi profit saat expiry (proxy dari delta) |
| **Chief Supervisor** | Agent LLM yang mengambil keputusan strategi final (di sistem ini: Strategy Decision Agent) |
| **Sequential Loading** | Load-run-unload model LLM satu per satu untuk efisiensi VRAM |
| **Single-cycle Pipeline** | Satu alur keputusan penuh dari Quant → Agent → Risk → Execution, tanpa dual fast/slow loop |
| **Paper Trading** | Simulasi trading tanpa uang riil, disediakan Alpaca untuk testing strategi |

---

## 16. Referensi

- Alpaca AI Trading Agents Hackathon (lablab.ai): https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon
- Alpaca Skills (agent skill library): https://github.com/alpacahq/alpaca-skills
- Getting Started with Trading API: https://docs.alpaca.markets/us/docs/getting-started-with-trading-api
- Getting Started with Market Data API: https://docs.alpaca.markets/us/docs/getting-started-with-alpaca-market-data
- Alpaca Trade API (JS/Node SDK): https://github.com/alpacahq/alpaca-trade-api-js
- Alpaca-py (Python SDK resmi): https://github.com/alpacahq/alpaca-py
- Alpaca CLI: https://github.com/alpacahq/cli
- Alpaca CLI Docs: https://docs.alpaca.markets/us/docs/alpacas-cli
- Alpaca MCP Server (Trading MCP): https://docs.alpaca.markets/us/docs/alpaca-mcp-server
- "Building a Multi-Agent AI Trading System on Alpaca" (Alpaca Learn): https://alpaca.markets/learn/building-a-multi-agent-ai-trading-system-on-alpaca
- SDKs and Tools overview: https://docs.alpaca.markets/us/docs/sdks-and-tools
- Alpaca-py Options Trading example notebook: https://github.com/alpacahq/alpaca-py/blob/master/examples/options/options-trading-basic.ipynb
- Options Trading Overview (Alpaca Docs): https://docs.alpaca.markets/us/docs/options-trading-overview
- Ollama (LLM lokal): https://ollama.ai

---

*PRD v1.0 | Autonomous Multi-Agent Options Trading System | Alpaca AI Trading Agents Hackathon 2026*
