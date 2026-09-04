from datetime import date
import json
import multiprocessing
from decimal import Decimal
from datetime import datetime, timezone

import numpy as np
import config
from execution import ledger


def _hold_ledger_lock(state_dir, started, release):
    import config as child_config

    child_config.STATE_DIR = state_dir
    with ledger.ledger_transaction():
        started.set()
        release.wait(5)


def _probe_ledger_lock(state_dir, ready, acquired):
    import config as child_config

    child_config.STATE_DIR = state_dir
    ready.set()
    with ledger.ledger_transaction():
        acquired.set()


def test_daily_stats_uses_exchange_market_date(monkeypatch):
    monkeypatch.setattr(config, "market_date", lambda: date(2026, 9, 1))

    data = {"positions": [], "daily": {}}

    assert ledger.daily_stats(data) == {"realized_pl": 0.0, "rejected_streak": 0}
    assert "2026-09-01" in data["daily"]


def test_save_normalizes_numpy_integer_scalars(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.json", raising=False)

    ledger.save({
        "positions": [{"qty": np.int32(1)}],
        "daily": {},
    })

    assert json.loads((tmp_path / "ledger.json").read_text())["positions"][0]["qty"] == 1


def test_save_normalizes_numpy_float_arrays_and_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.json", raising=False)

    ledger.save({
        "positions": [{
            "ratio": np.float32(0.5),
            "levels": np.array([1.25, 2.5]),
            "debit": Decimal("1.20"),
            "opened": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "expiry": date(2026, 9, 4),
        }],
        "daily": {},
    })

    saved = json.loads((tmp_path / "ledger.json").read_text())["positions"][0]
    assert saved["ratio"] == 0.5
    assert saved["levels"] == [1.25, 2.5]
    assert saved["debit"] == "1.20"
    assert saved["opened"] == "2026-09-01T00:00:00+00:00"
    assert saved["expiry"] == "2026-09-04"


def test_ledger_transaction_serializes_process_writers(tmp_path):
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    release = context.Event()
    ready = context.Event()
    acquired = context.Event()
    holder = context.Process(
        target=_hold_ledger_lock,
        args=(tmp_path, started, release),
    )
    probe = context.Process(
        target=_probe_ledger_lock,
        args=(tmp_path, ready, acquired),
    )

    holder.start()
    assert started.wait(5)
    probe.start()
    assert ready.wait(5)
    assert not acquired.wait(0.5)
    release.set()
    assert acquired.wait(5)
    holder.join(5)
    probe.join(5)
    assert holder.exitcode == 0
    assert probe.exitcode == 0
