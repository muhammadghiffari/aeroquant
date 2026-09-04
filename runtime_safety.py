"""Fail-closed checks for autonomous paper execution."""
from __future__ import annotations

import config


def configuration_errors(
    require_llm: bool = False,
    require_autonomy: bool = False,
    require_telegram: bool = False,
) -> list[str]:
    """Return safe-to-log configuration errors without exposing secret values."""
    errors: list[str] = []
    if not config.PAPER_TRADE:
        errors.append("ALPACA_PAPER_TRADE must be true")
    if not config.ALPACA_PAPER_API_KEY:
        errors.append("ALPACA_PAPER_API_KEY is missing")
    if not config.ALPACA_PAPER_SECRET_KEY:
        errors.append("ALPACA_PAPER_SECRET_KEY is missing")
    if not config.EXPECTED_ALPACA_ACCOUNT_ID:
        errors.append("ALPACA_EXPECTED_ACCOUNT_ID is missing")
    if require_autonomy and not config.AUTONOMOUS_TRADING_ENABLED:
        errors.append("AUTONOMOUS_TRADING_ENABLED must be true")
    if require_telegram:
        if not config.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is missing")
        if not config.TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_CHAT_ID is missing")

    if require_llm:
        if config.LLM_PROVIDER == "featherless":
            if not config.FEATHERLESS_API_KEY:
                errors.append("FEATHERLESS_API_KEY is missing")
            if not config.FEATHERLESS_MODEL:
                errors.append("FEATHERLESS_MODEL is missing")
        elif config.LLM_PROVIDER == "anthropic":
            if not config.ANTHROPIC_API_KEY:
                errors.append("ANTHROPIC_API_KEY is missing")
        elif config.LLM_PROVIDER == "ollama":
            errors.append("Ollama is not permitted for the remote-first autonomous runtime")
        else:
            errors.append(f"unsupported LLM_PROVIDER: {config.LLM_PROVIDER}")
    return errors


def account_identity_error(account, expected_account_id: str | None = None) -> str | None:
    """Ensure a broker response belongs to the configured dedicated account."""
    expected = (expected_account_id if expected_account_id is not None else config.EXPECTED_ALPACA_ACCOUNT_ID).strip()
    if not expected:
        return "expected account ID is not configured"
    actual = str(getattr(account, "id", "") or "").strip()
    if not actual:
        return "broker account ID is unavailable"
    if actual != expected:
        return "broker account ID mismatch"
    return None
