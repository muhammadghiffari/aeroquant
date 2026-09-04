"""Shared Alpaca clients with retry + exponential backoff."""
import logging
import time
from functools import wraps

from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

import config

log = logging.getLogger(__name__)

_clients: dict = {}


def _retry(max_attempts: int = 3, base_delay: float = 2.0):
    def _retryable(exc) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
        try:
            return not (400 <= int(status_code) < 500)
        except (TypeError, ValueError):
            return True

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    if not _retryable(exc):
                        raise
                    wait = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "%s failed (attempt %d/%d): %s -- retrying in %.0fs",
                        fn.__name__, attempt, max_attempts, exc, wait,
                    )
                    if attempt < max_attempts:
                        time.sleep(wait)
            raise last  # type: ignore[misc]
        return wrapper
    return deco


def trading_client() -> TradingClient:
    if "trade" not in _clients:
        _clients["trade"] = TradingClient(
            config.ALPACA_PAPER_API_KEY,
            config.ALPACA_PAPER_SECRET_KEY,
            paper=config.PAPER_TRADE,
        )
    return _clients["trade"]


def option_data_client() -> OptionHistoricalDataClient:
    if "option" not in _clients:
        _clients["option"] = OptionHistoricalDataClient(
            config.ALPACA_PAPER_API_KEY, config.ALPACA_PAPER_SECRET_KEY
        )
    return _clients["option"]


def stock_data_client() -> StockHistoricalDataClient:
    if "stock" not in _clients:
        _clients["stock"] = StockHistoricalDataClient(
            config.ALPACA_PAPER_API_KEY, config.ALPACA_PAPER_SECRET_KEY
        )
    return _clients["stock"]


def crypto_data_client() -> CryptoHistoricalDataClient:
    if "crypto" not in _clients:
        _clients["crypto"] = CryptoHistoricalDataClient(
            config.ALPACA_PAPER_API_KEY, config.ALPACA_PAPER_SECRET_KEY
        )
    return _clients["crypto"]


def news_client() -> NewsClient:
    if "news" not in _clients:
        _clients["news"] = NewsClient(
            config.ALPACA_PAPER_API_KEY, config.ALPACA_PAPER_SECRET_KEY
        )
    return _clients["news"]


@_retry()
def safe(fn_name: str, call, *args, **kwargs):
    """Call any SDK method through the retry wrapper."""
    return call(*args, **kwargs)
