from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts.reset_runtime import broker_state_is_clear, clear_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_broker_state_is_clear_accepts_empty_paper_account():
    assert broker_state_is_clear(SimpleNamespace(id="acct-1"), [], []) is True


def test_broker_state_is_clear_refuses_open_position():
    with pytest.raises(RuntimeError, match="open position"):
        broker_state_is_clear(
            SimpleNamespace(id="acct-1"), [{"symbol": "SPY"}], []
        )


def test_broker_state_is_clear_refuses_open_order():
    with pytest.raises(RuntimeError, match="open order"):
        broker_state_is_clear(
            SimpleNamespace(id="acct-1"), [], [{"status": "new"}]
        )


def test_clear_runtime_removes_children_and_keeps_roots(tmp_path):
    roots = [tmp_path / name for name in ("state", "reports", "runs")]
    for root in roots:
        root.mkdir()
        (root / "old.json").write_text("old", encoding="utf-8")
        (root / "nested").mkdir()
        (root / "nested" / "old.log").write_text("old", encoding="utf-8")

    clear_runtime(roots)

    assert all(root.exists() and not list(root.iterdir()) for root in roots)


def test_reset_wrapper_stops_both_workers_before_python_reset():
    wrapper = (PROJECT_ROOT / "scripts" / "reset_runtime.ps1").read_text()

    assert "AeroQuant-Radith-Momentum" in wrapper
    assert "AeroQuant-Radith-Monitor" in wrapper
    assert "Stop-ScheduledTask" in wrapper
    assert "-m scripts.reset_runtime --confirm-reset" in wrapper
