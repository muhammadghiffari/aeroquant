"""Guarded reset of local runtime artifacts for the paper account."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from data_engine import alpaca_client
from runtime_safety import account_identity_error, configuration_errors


_TERMINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced"}


def _value(item, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _status_name(status) -> str:
    return str(status or "").split(".")[-1].lower()


def broker_state_is_clear(account, positions, orders) -> bool:
    if not _value(account, "id"):
        raise RuntimeError("broker account ID is unavailable")
    if positions:
        raise RuntimeError("cannot reset with open position(s)")
    open_orders = [
        order for order in orders
        if _status_name(_value(order, "status")) not in _TERMINAL_ORDER_STATUSES
    ]
    if open_orders:
        raise RuntimeError("cannot reset with open order(s)")
    return True


def runtime_roots() -> tuple[Path, ...]:
    return (
        config.STATE_DIR,
        config.REPORTS_DIR,
        config.BASE_DIR / "runs",
    )


def clear_runtime(roots) -> None:
    for root in (Path(path) for path in roots):
        root.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)


def verify_broker_is_clear() -> None:
    errors = configuration_errors()
    if errors:
        raise RuntimeError("runtime configuration: " + "; ".join(errors))
    client = alpaca_client.trading_client()
    account = alpaca_client.safe("get_account", client.get_account)
    identity_error = account_identity_error(account)
    if identity_error:
        raise RuntimeError(identity_error)
    positions = alpaca_client.safe("get_all_positions", client.get_all_positions)
    orders = alpaca_client.safe("get_orders", client.get_orders)
    broker_state_is_clear(account, positions, orders)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reset local paper-trading runtime data")
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="confirm deletion of state, reports, and runs contents",
    )
    args = parser.parse_args(argv)
    if not args.confirm_reset:
        parser.error("--confirm-reset is required")
    verify_broker_is_clear()
    clear_runtime(runtime_roots())
    print("runtime reset complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
