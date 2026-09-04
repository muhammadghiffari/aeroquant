"""Central configuration - Autonomous Multi-Agent Options Trading System v1.0."""
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
MARKET_TIMEZONE = ZoneInfo("America/New_York")


def market_date() -> date:
    """Return the current date in the exchange's timezone."""
    return datetime.now(MARKET_TIMEZONE).date()

# --- Alpaca -----------------------------------------------------------------
ALPACA_PAPER_API_KEY = os.getenv("ALPACA_PAPER_API_KEY", "")
ALPACA_PAPER_SECRET_KEY = os.getenv("ALPACA_PAPER_SECRET_KEY", "")
EXPECTED_ALPACA_ACCOUNT_ID = os.getenv("ALPACA_EXPECTED_ACCOUNT_ID", "").strip()
AUTONOMOUS_TRADING_ENABLED = os.getenv("AUTONOMOUS_TRADING_ENABLED", "false").strip().lower() == "true"

PAPER_TRADE = os.getenv("ALPACA_PAPER_TRADE", "true").strip().lower() == "true"
TRADE_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"

if not PAPER_TRADE:
    raise SystemExit(
        "SAFETY: ALPACA_PAPER_TRADE must be true in v1.0. Live trading is not enabled."
    )

# --- Universe ---------------------------------------------------------------
WATCHLIST_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]

# --- Quant thresholds --------------------------------------------------------
IV_RANK_HIGH = 70.0   # >= this -> premium expensive -> sell premium bias
IV_RANK_LOW = 30.0    # <= this -> premium cheap -> buy premium bias
MIN_LIQUIDITY_OI = 100
MAX_BID_ASK_SPREAD_PCT = 0.10
MOMENTUM_MIN_DTE = 7
MOMENTUM_MAX_DTE = 21
MOMENTUM_MIN_DELTA = 0.45
MOMENTUM_MAX_DELTA = 0.70
MOMENTUM_MAX_SPREAD_PCT = 0.05
MOMENTUM_MIN_PROBABILITY_LB = 0.60
MOMENTUM_PROXY_MIN_PROBABILITY = float(os.getenv("MOMENTUM_PROXY_MIN_PROBABILITY", "0.50"))
CONTRACT_CONFIDENCE_MIN_SAMPLES = int(os.getenv("CONTRACT_CONFIDENCE_MIN_SAMPLES", "30"))
CONTRACT_CONFIDENCE_HORIZON = int(os.getenv("CONTRACT_CONFIDENCE_HORIZON", "1"))
SHADOW_MAX_CANDIDATES = int(os.getenv("SHADOW_MAX_CANDIDATES", "3"))
SHADOW_MIN_DELTA = float(os.getenv("SHADOW_MIN_DELTA", "0.45"))
SHADOW_MAX_DELTA = float(os.getenv("SHADOW_MAX_DELTA", "0.70"))
SHADOW_ANALYSIS_ENABLED = os.getenv("SHADOW_ANALYSIS_ENABLED", "false").strip().lower() == "true"
MOMENTUM_MIN_EXPECTED_VALUE = 0.0
MOMENTUM_MAX_QUOTE_AGE_SECONDS = int(os.getenv("MOMENTUM_MAX_QUOTE_AGE_SECONDS", "30"))
EXIT_QUOTE_MAX_AGE_SECONDS = int(os.getenv("EXIT_QUOTE_MAX_AGE_SECONDS", "30"))
NEWS_REFRESH_INTERVAL_SECONDS = int(os.getenv("NEWS_REFRESH_INTERVAL_SECONDS", "300"))
NEWS_MIN_CONFIDENCE = float(os.getenv("NEWS_MIN_CONFIDENCE", "0.50"))
CRITICAL_NEWS_MIN_CONFIDENCE = float(os.getenv("CRITICAL_NEWS_MIN_CONFIDENCE", "0.75"))
CRITICAL_NEWS_MAX_AGE_SECONDS = int(os.getenv("CRITICAL_NEWS_MAX_AGE_SECONDS", "900"))

# --- Risk management (rule-based, non-negotiable) ----------------------------
MAX_LOSS_PCT_PER_TRADE = 0.03      # 3% of buying power
MAX_EXPOSURE_PCT_PER_SYMBOL = 0.15
MAX_OPEN_POSITIONS_TOTAL = 6
MIN_DTE = 1                        # allow near-dated weeklies (short trading window)
MAX_DTE = 21                       # entry horizon; exits remain rule-driven
PRE_EXPIRY_CLOSE_DAYS = 1          # hard-close positions D-1 before expiry
FINAL_CLOSE_DATE = date.fromisoformat(os.getenv("FINAL_CLOSE_DATE", "2026-09-04"))
LONG_OPTION_MIN_PROFIT_PCT = float(os.getenv("LONG_OPTION_MIN_PROFIT_PCT", "0.01"))
LONG_OPTION_TAKE_PROFIT_PCT = float(os.getenv("LONG_OPTION_TAKE_PROFIT_PCT", "0.35"))
LONG_OPTION_STOP_LOSS_PCT = float(os.getenv("LONG_OPTION_STOP_LOSS_PCT", "0.50"))

# Kill switch / circuit breaker
DAILY_MAX_REJECTED_IN_ROW = 5
DAILY_MAX_LOSS_PCT = 0.05          # stop new entries after 5% equity day loss

# Order pricing
LIMIT_PRICE_SLIPPAGE_PCT = 0.15    # pad mid by up to 15% toward adverse side (fill priority)

# --- LLM (remote-first: featherless | openai | anthropic) ---------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "featherless").strip().lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "30m")     # ollama only; "5m" for small VPS
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")
FEATHERLESS_BASE_URL = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
FEATHERLESS_MODEL = os.getenv("FEATHERLESS_MODEL", "")
FEATHERLESS_LIGHT_MODEL = os.getenv("FEATHERLESS_LIGHT_MODEL", "")
FEATHERLESS_HEAVY_MODEL = os.getenv("FEATHERLESS_HEAVY_MODEL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
LLM_TIMEOUT_S = 120
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))  # token-thrift cap per call
LLM_JSON_RETRY = 1
LLM_ENABLED = True                 # auto-disabled if provider unreachable at startup

# --- Alpaca MCP Server (hackathon requirement) ---------------------------------
MCP_ENABLED = os.getenv("MCP_ENABLED", "true").strip().lower() == "true"
MCP_TOOLSETS = "news,assets,stock-data"   # READ-ONLY toolsets; execution stays alpaca-py
MCP_TIMEOUT_S = 60

# --- Evaluation & memory --------------------------------------------------------
EVALUATION_ENABLED = os.getenv("EVALUATION_ENABLED", "true").strip().lower() == "true"
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "disabled").strip().lower()
EMBED_MODEL = os.getenv("EMBED_MODEL", "")
LESSONS_MAX = 10                   # max lessons injected into chief prompt

# --- Server --------------------------------------------------------------------
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))

# --- Cycle timing --------------------------------------------------------------
CYCLE_INTERVAL_MIN = 5
MONITOR_LOOKBACK_MIN = 240
MOMENTUM_MIN_SAMPLES = 30
MOMENTUM_HORIZON = 1
REVERSAL_HISTORY_MAX = 4
MARKET_HOURS_ONLY = True

# --- BTC market context -------------------------------------------------------
BTC_SYMBOL = os.getenv("BTC_SYMBOL", "BTC/USD")
BTC_CONTEXT_LOOKBACK_MIN = int(os.getenv("BTC_CONTEXT_LOOKBACK_MIN", "360"))
BTC_CONTEXT_MAX_AGE_SECONDS = int(os.getenv("BTC_CONTEXT_MAX_AGE_SECONDS", "300"))
BTC_EXTREME_ZSCORE = float(os.getenv("BTC_EXTREME_ZSCORE", "2.5"))
BTC_EXTREME_RETURN_1H_PCT = float(os.getenv("BTC_EXTREME_RETURN_1H_PCT", "1.5"))
BTC_CONFLICT_SIZE_MULTIPLIER = float(os.getenv("BTC_CONFLICT_SIZE_MULTIPLIER", "0.5"))
BTC_EXTREME_SIZE_MULTIPLIER = float(os.getenv("BTC_EXTREME_SIZE_MULTIPLIER", "0.5"))
BTC_SHADOW_ONLY = os.getenv("BTC_SHADOW_ONLY", "true").strip().lower() == "true"

# --- Storage --------------------------------------------------------------------
REPORTS_DIR = BASE_DIR / "reports"
STATE_DIR = BASE_DIR / "state"
LEDGER_PATH = STATE_DIR / "ledger.json"
OPERATIONAL_DB_PATH = STATE_DIR / "operational.db"

REPORTS_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)
