"""Provider factory + availability probing (singleton per process)."""
import logging
import threading

import config
from llm.providers import (
    AnthropicProvider,
    BaseProvider,
    FeatherlessProvider,
    OllamaProvider,
    OpenAIProvider,
    ProviderError,
    _PROVIDERS,
)

log = logging.getLogger(__name__)
_lock = threading.Lock()
_provider: BaseProvider | None = None


def _build() -> BaseProvider:
    kind = config.LLM_PROVIDER
    cls = _PROVIDERS.get(kind)
    if cls is None:
        raise ProviderError(f"unknown LLM_PROVIDER '{kind}' (want: {sorted(_PROVIDERS)})")
    return cls()


def get_provider() -> BaseProvider:
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = _build()
                log.info("LLM provider: %s", _provider.name)
    return _provider


def reset_provider() -> None:
    global _provider
    with _lock:
        _provider = None


def provider_available() -> bool:
    """Cheap reachability check; cloud providers are available iff configured."""
    try:
        p = get_provider()
    except ProviderError as exc:
        log.warning("LLM provider unavailable: %s", exc)
        return False
    if isinstance(p, OllamaProvider):
        return p.available()
    return True


__all__ = ["get_provider", "reset_provider", "provider_available", "ProviderError"]
