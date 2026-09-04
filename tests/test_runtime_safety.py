from types import SimpleNamespace

import config
from alerts import send_alert
from runtime_safety import account_identity_error, configuration_errors


def test_configuration_errors_require_paper_account_identity_and_llm(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADE", True)
    monkeypatch.setattr(config, "ALPACA_PAPER_API_KEY", "configured")
    monkeypatch.setattr(config, "ALPACA_PAPER_SECRET_KEY", "configured")
    monkeypatch.setattr(config, "EXPECTED_ALPACA_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "LLM_PROVIDER", "featherless")
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    monkeypatch.setattr(config, "FEATHERLESS_MODEL", "")

    errors = configuration_errors(require_llm=True)

    assert "ALPACA_EXPECTED_ACCOUNT_ID is missing" in errors
    assert "FEATHERLESS_API_KEY is missing" in errors
    assert "FEATHERLESS_MODEL is missing" in errors
    assert all("configured" not in error for error in errors)


def test_account_identity_rejects_missing_or_mismatched_account():
    assert account_identity_error(SimpleNamespace(id="acct-1"), "") == "expected account ID is not configured"
    assert account_identity_error(SimpleNamespace(id="acct-1"), "acct-2") == "broker account ID mismatch"
    assert account_identity_error(SimpleNamespace(id="acct-1"), "acct-1") is None


def test_telegram_alert_is_disabled_without_real_credentials(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")

    result = send_alert("test", "not sent")

    assert result == {"sent": False, "reason": "not_configured"}


def test_configuration_errors_can_require_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")

    errors = configuration_errors(require_telegram=True)

    assert "TELEGRAM_BOT_TOKEN is missing" in errors
    assert "TELEGRAM_CHAT_ID is missing" in errors
