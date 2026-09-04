import pytest

from data_engine import alpaca_client


def test_safe_does_not_retry_client_errors(monkeypatch):
    attempts = []

    class ClientError(Exception):
        status_code = 403

    def fail():
        attempts.append(1)
        raise ClientError("forbidden")

    monkeypatch.setattr(alpaca_client.time, "sleep", lambda _seconds: None)

    with pytest.raises(ClientError):
        alpaca_client.safe("submit_order", fail)

    assert len(attempts) == 1
