"""Autonomous evaluation layer.

- store.py    : SQLite -- hard numbers (trades, win rates) synced from ledger
- memory.py   : LanceDB -- semantic post-mortem memory (why past trades won/lost)
- evaluator.py: closes the loop -- post-mortems, lessons.json, stats for the chief
"""
from evaluation.evaluator import run_after_cycle

__all__ = ["run_after_cycle"]
