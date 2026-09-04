"""Tests for the fixed paper-trading mandate."""
import config


def test_fixed_watchlist_contains_the_agreed_five_symbols():
    assert config.WATCHLIST_SYMBOLS == ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]


def test_local_model_and_final_close_configuration_are_explicit():
    assert config.OLLAMA_MODEL == "qwen3.5:9b"
    assert config.FINAL_CLOSE_DATE.isoformat() == "2026-09-04"
    assert config.LONG_OPTION_STOP_LOSS_PCT == 0.50
    assert config.LONG_OPTION_MIN_PROFIT_PCT > 0


def test_shadow_analysis_is_off_and_quant_dte_fits_risk_dte():
    assert config.SHADOW_ANALYSIS_ENABLED is False
    assert config.MOMENTUM_MAX_DTE == 21
    assert config.MAX_DTE == 21
