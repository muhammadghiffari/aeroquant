"""SQLite operational audit tests."""
import config
from execution import operational_store


def test_order_intent_is_idempotent_and_transitions_to_acknowledged(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OPERATIONAL_DB_PATH", tmp_path / "operational.db")

    operational_store.record_cycle("cycle-1", "account-1")
    operational_store.record_cycle("cycle-1", "account-1")
    operational_store.create_order_intent(
        "intent-1", "cycle-1", "account-1", "agent-spy-1", {"symbol": "SPY"}
    )
    operational_store.record_order_ack("intent-1", "order-1", "accepted")

    assert operational_store.get_cycle_count() == 1
    intent = operational_store.get_order_intent("intent-1")
    assert intent["status"] == "ACKNOWLEDGED"
    assert intent["broker_order_id"] == "order-1"


def test_store_finds_unresolved_intent_by_account_and_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OPERATIONAL_DB_PATH", tmp_path / "operational.db")

    operational_store.create_order_intent(
        "intent-1", "cycle-1", "account-1", "agent-spy-1", {"symbol": "SPY"}
    )

    found = operational_store.find_unresolved_intent("account-1", "SPY")

    assert found["intent_id"] == "intent-1"


def test_store_marks_intent_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OPERATIONAL_DB_PATH", tmp_path / "operational.db")

    operational_store.create_order_intent(
        "intent-1", "cycle-1", "account-1", "agent-spy-1", {"symbol": "SPY"}
    )
    operational_store.update_order_status("intent-1", "FILLED", "filled")

    assert operational_store.get_order_intent("intent-1")["status"] == "FILLED"
