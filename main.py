"""Entry point.

Examples:
  python main.py --once --symbol SPY
  python main.py --once --force            # run even when market closed
  python main.py --loop --interval 5       # autonomous mode
  python main.py --once --symbol SPY --dry-run  # local analysis, no order submission
  python main.py --serve                   # dashboard only
"""
import argparse
import logging
import os
from datetime import datetime

import config


_LOOP_LOCK_PATH = config.STATE_DIR / "momentum_loop.lock"


def _write_loop_event(message: str) -> None:
    try:
        with open(config.STATE_DIR / "entry_loop.log", "a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().isoformat()} {message}\n")
    except OSError:
        pass


def _acquire_loop_mutex():
    """Allow only one autonomous entry loop across Windows sessions."""
    if os.name != "nt":
        return False

    import msvcrt

    lock_file = open(_LOOP_LOCK_PATH, "a+b")
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def _release_loop_mutex(handle) -> None:
    if os.name != "nt" or not handle:
        return

    import msvcrt

    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _monitor_once(dry_run: bool = False) -> list[dict]:
    """Run deterministic position exits without starting a decision cycle."""
    if dry_run:
        return []
    from execution import ledger, position_manager
    from alerts import flush_telegram_outbox

    flush_telegram_outbox()
    with ledger.ledger_transaction():
        data = ledger.load()
        exits = position_manager.manage_positions(data)
        ledger.save(data)
    return exits


def main() -> None:
    ap = argparse.ArgumentParser(description="Autonomous multi-agent options trading bot (paper)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run a single cycle")
    mode.add_argument("--loop", action="store_true", help="run cycles continuously")
    mode.add_argument("--monitor", action="store_true", help="monitor positions and run exit rules only")
    mode.add_argument("--serve", action="store_true", help="dashboard web server only")
    ap.add_argument("--symbol", type=str, default=None,
                    help="comma-separated symbols")
    ap.add_argument("--interval", type=int, default=config.CYCLE_INTERVAL_MIN,
                    help="minutes between cycles in loop mode")
    ap.add_argument("--force", action="store_true",
                    help="run even if market is closed (stale-data warning)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run analysis without submitting orders or changing the ledger")
    args = ap.parse_args()

    _setup_logging()

    symbols = [s.strip().upper() for s in (args.symbol or ",".join(config.WATCHLIST_SYMBOLS)).split(",") if s.strip()]

    if args.serve:
        import uvicorn

        from server import app

        uvicorn.run(app, host="0.0.0.0", port=config.DASHBOARD_PORT, log_level="warning")
        return

    if args.monitor:
        import time

        while True:
            try:
                exits = _monitor_once(dry_run=args.dry_run)
                if exits:
                    logging.getLogger(__name__).info("monitor exits: %s", exits)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).exception("position monitor crashed -- continuing")
                from alerts import send_alert
                send_alert("monitor_process_error", str(exc))
            time.sleep(max(1, args.interval) * 60)

    from orchestrator.pipeline import run_cycle

    if args.once:
        report = run_cycle(symbols, force=args.force, dry_run=args.dry_run)
        skipped = report.get("skipped")
        print(f"cycle {report['cycle_id']}: {skipped or 'completed'}")
        return

    # loop mode
    import time

    if not args.dry_run:
        from runtime_safety import configuration_errors
        from alerts import telegram_health_check

        runtime_errors = configuration_errors(
            require_llm=True, require_autonomy=True, require_telegram=True
        )
        if not runtime_errors:
            telegram_ok, telegram_reason = telegram_health_check()
            if not telegram_ok:
                runtime_errors.append(f"TELEGRAM_HEALTHCHECK_FAILED: {telegram_reason}")
        _write_loop_event(f"runtime_errors={runtime_errors}")
        if runtime_errors:
            logging.getLogger(__name__).error("runtime safety check failed: %s", "; ".join(runtime_errors))
            return

    loop_mutex = _acquire_loop_mutex()
    _write_loop_event(f"mutex_acquired={loop_mutex is not None}")
    if loop_mutex is None:
        logging.getLogger(__name__).error(
            "another autonomous momentum loop is already running; exiting duplicate"
        )
        return
    try:
        while True:
            try:
                run_cycle(symbols, force=args.force, dry_run=args.dry_run)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).exception("cycle crashed -- continuing")
                from alerts import send_alert
                send_alert("cycle_process_error", str(exc))
            time.sleep(args.interval * 60)
    finally:
        _release_loop_mutex(loop_mutex)


if __name__ == "__main__":
    main()
