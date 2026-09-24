"""Snapshot IBKR broker state (paper + live Gateways) into ClickHouse ``broker.*``.

Each snapshot is one ``meta.ingestion_runs`` row produced by
``IBKRBrokerProvider`` through the shared provider contract: every Gateway
response is archived to ``raw.archive`` (``transport='tcp_socket'``) and the
``broker.positions_snapshot`` / ``account_state_snapshot`` / ``executions`` /
``open_orders_snapshot`` rows are normalized from those archived bytes.

A Gateway that is down (logged out, restarting, awaiting 2FA) marks only its
own units failed; the run finishes ``partial``, ``meta.source_status`` for
``ibkr`` reports it, and a notification is sent.

Usage:
    python scripts/us/ibkr/us_portfolio_ibkr_snapshot.py --once
    python scripts/us/ibkr/us_portfolio_ibkr_snapshot.py --once --dry-run --modes paper
    python scripts/us/ibkr/us_portfolio_ibkr_snapshot.py --daemon --run-on-start   # production

Daemon slots default to 06:00 and 16:30 America/New_York on XNYS sessions
(``--at`` or ``IBKR_SNAPSHOT_TIMES`` override).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from factorlab.shared.ingest.provider import RunSummary, run_provider
from factorlab.shared.runtime import (
    ExitCode,
    GracefulShutdown,
    Heartbeat,
    acquire_lock,
    supervised,
)
from factorlab.shared.runtime.us_calendar import NY, next_scheduled_run
from factorlab.sources.ibkr.provider import (
    SNAPSHOT_CLIENT_ID,
    DryRunBrokerStorage,
    IBKRBrokerProvider,
    SnapshotConfig,
)
from factorlab.sources.ibkr.shapes import Mode

SERVICE = "ibkr_broker_snapshot"
DEFAULT_TIMES = "06:00,16:30"
TICK_SECONDS = 30.0
STATUS_BY_RUN = {"success": "ready", "partial": "incomplete", "failed": "error",
                 "cancelled": "stopped"}

log = logging.getLogger("factorlab.ibkr.snapshot")


def parse_modes(raw: str) -> tuple[Mode, ...]:
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    for token in tokens:
        if token not in ("paper", "live"):
            raise argparse.ArgumentTypeError(f"invalid mode {token!r} (want 'paper' or 'live')")
    if not tokens:
        raise argparse.ArgumentTypeError("at least one mode is required")
    return tuple(dict.fromkeys(tokens))  # type: ignore[return-value]


def parse_times(raw: str) -> tuple[time, ...]:
    slots = []
    for token in (t.strip() for t in raw.split(",") if t.strip()):
        try:
            slots.append(time.fromisoformat(token))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid HH:MM slot {token!r}") from exc
    if not slots:
        raise argparse.ArgumentTypeError("at least one schedule slot is required")
    return tuple(sorted(set(slots)))


def describe(summary: RunSummary) -> str:
    if not summary.failed_units:
        return f"IBKR snapshot ok: {summary.rows_written} rows across {len(summary.units)} units"
    failed = ", ".join(unit.name for unit in summary.failed_units)
    return (f"IBKR snapshot {summary.status}: {summary.rows_written} rows; "
            f"failed units: {failed}")


def report(storage: Any, summary: RunSummary, *, dry_run: bool) -> None:
    """Publish the run outcome to logs, ``meta.source_status`` and notifications."""
    detail = describe(summary)
    for unit in summary.units:
        log.info("  %-24s rows=%-6d %s", unit.name, unit.rows_written,
                 "ok" if unit.ok else f"FAILED: {unit.error}")
    log.info("%s (run_id=%s)", detail, summary.run_id)
    if dry_run:
        return
    try:
        storage.source_status(STATUS_BY_RUN[summary.status], detail, source="ibkr")
    except Exception:
        log.exception("Could not write meta.source_status for ibkr")
    if summary.status != "success":
        _notify(f"IBKR snapshot {summary.status}", detail,
                severity="warn" if summary.status == "partial" else "fail",
                context={"run_id": str(summary.run_id),
                         **{u.name: u.error for u in summary.failed_units}})


def _notify(subject: str, body: str, *, severity: str, context: dict[str, Any]) -> None:
    try:
        from factorlab.shared.notify import notify

        notify(subject, body, severity=severity, source=SERVICE, vendor="ibkr",
               country="US", context=context, dedupe_key=f"{SERVICE}|{subject}")
    except Exception:
        log.exception("Notification failed for %s", subject)


def run_once(storage: Any, config: SnapshotConfig, *, dry_run: bool = False) -> RunSummary:
    provider = IBKRBrokerProvider(storage, config)
    summary = run_provider(
        provider, storage, universe="ibkr_accounts",
        requested_series=len(config.modes) * 4,
        metadata={"modes": list(config.modes), "client_id": config.client_id,
                  "executions_lookback_hours": config.executions_lookback.total_seconds() / 3600},
    )
    report(storage, summary, dry_run=dry_run)
    return summary


def run_daemon(
    storage: Any,
    config: SnapshotConfig,
    slots: Sequence[time],
    *,
    should_stop: Callable[[], bool],
    run_on_start: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None],
    heartbeat: Callable[[], None] = lambda: None,
    snapshot: Callable[[], Any] | None = None,
) -> int:
    """Run a snapshot at each scheduled slot until ``should_stop()``; never crash on a bad run."""
    take = snapshot or (lambda: run_once(storage, config))
    pending_start = run_on_start
    while not should_stop():
        if pending_start:
            pending_start = False
        else:
            due = next_scheduled_run(clock(), slots)
            log.info("Next IBKR snapshot at %s (%s New York)", due.isoformat(),
                     due.astimezone(NY).strftime("%a %H:%M"))
            try:
                storage.source_status("waiting", f"Next snapshot at {due.isoformat()}",
                                      source="ibkr")
            except Exception:
                log.warning("Could not write meta.source_status for ibkr", exc_info=True)
            while not should_stop() and clock() < due:
                heartbeat()
                sleep(min(TICK_SECONDS, max((due - clock()).total_seconds(), 0.0)))
            if should_stop():
                break
        heartbeat()
        try:
            take()
        except Exception as exc:
            log.exception("IBKR snapshot run failed")
            _notify("IBKR snapshot crashed", f"{type(exc).__name__}: {exc}",
                    severity="fail", context={})
    try:
        storage.source_status("stopped", "IBKR snapshot daemon stopped", source="ibkr")
    except Exception:
        log.warning("Could not write meta.source_status for ibkr", exc_info=True)
    return int(ExitCode.OK)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument("--once", action="store_true", help="Take one snapshot and exit (default)")
    run_mode.add_argument("--daemon", action="store_true", help="Snapshot at each scheduled slot")
    parser.add_argument("--modes", type=parse_modes, default=parse_modes("paper,live"),
                        help="Comma-separated Gateway modes (default: paper,live)")
    parser.add_argument("--at", type=parse_times,
                        default=parse_times(os.getenv("IBKR_SNAPSHOT_TIMES", DEFAULT_TIMES)),
                        help="Daemon slots, HH:MM America/New_York (default: 06:00,16:30)")
    parser.add_argument("--run-on-start", action="store_true",
                        help="Daemon: take a snapshot immediately, then follow the schedule")
    parser.add_argument("--client-id", type=int, default=SNAPSHOT_CLIENT_ID,
                        help=f"IBKR API clientId (default: {SNAPSHOT_CLIENT_ID})")
    parser.add_argument("--executions-lookback-hours", type=float, default=24.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="Connect, capture and normalize; write nothing to ClickHouse")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.getenv("FACTORLAB_SECRETS_DIR"):
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))  # local development only

    config = SnapshotConfig(
        modes=args.modes, client_id=args.client_id,
        executions_lookback=timedelta(hours=args.executions_lookback_hours),
    )
    lock_path = Path(os.getenv("IBKR_SNAPSHOT_LOCK", str(ROOT / "data" / "ibkr-snapshot.lock")))
    try:
        with acquire_lock(lock_path):
            storage = _storage(dry_run=args.dry_run)
            try:
                if not args.daemon:
                    summary = run_once(storage, config, dry_run=args.dry_run)
                    return int({"success": ExitCode.OK, "partial": ExitCode.WARN}.get(
                        summary.status, ExitCode.FATAL))
                heartbeat = Heartbeat(SERVICE)
                with GracefulShutdown(log) as shutdown:
                    return run_daemon(
                        storage, config, args.at,
                        should_stop=lambda: shutdown.triggered,
                        run_on_start=args.run_on_start,
                        sleep=_interruptible_sleep(lambda: shutdown.triggered),
                        heartbeat=heartbeat.tick,
                        snapshot=lambda: run_once(storage, config, dry_run=args.dry_run),
                    )
            finally:
                close = getattr(storage, "close", None)
                if close is not None:
                    close()
    except SystemExit as exc:
        if "another orchestrator holds" in str(exc):
            log.error("%s", exc)
            return int(ExitCode.LOCK_HELD)
        raise


def _storage(*, dry_run: bool) -> Any:
    if dry_run:
        return _DryRunStatusStorage()
    from factorlab.storage.v2_broker import V2BrokerStorage

    return V2BrokerStorage.from_environment()


class _DryRunStatusStorage(DryRunBrokerStorage):
    def source_status(self, status: str, detail: str, *, source: str) -> None:
        log.info("[dry-run] source_status %s=%s: %s", source, status, detail)


def _interruptible_sleep(stopped: Callable[[], bool]) -> Callable[[float], None]:
    import time as _time

    def sleep(seconds: float) -> None:
        deadline = _time.monotonic() + seconds
        while not stopped() and _time.monotonic() < deadline:
            _time.sleep(min(1.0, deadline - _time.monotonic()))

    return sleep


if __name__ == "__main__":
    sys.exit(supervised(main, name=SERVICE))
