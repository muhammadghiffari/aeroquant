"""Provider-agnostic LLM layer.

All agents talk to `get_provider().complete(...)` and receive structured JSON
plus token usage. Providers: featherless, openai, anthropic, and optional ollama.
Switching = one env var (LLM_PROVIDER) + API key in .env. Zero code changes.
"""
from llm.client import ProviderError, get_provider, provider_available, reset_provider

__all__ = ["get_provider", "reset_provider", "provider_available", "ProviderError"]
