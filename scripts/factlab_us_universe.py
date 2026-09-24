"""Resolve and publish the configured US equity collection universe."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factorlab.countries.us.equities.eodhd.configured_universe import (
    resolve_universe as legacy_resolve,
)
from factorlab.storage.v2_us import V2USStorage as USStorage
from factorlab.universe import create_resolver, load_config

log = logging.getLogger("factorlab.us-universe")
stop = threading.Event()
MINIMUM_MASTER_SIZE = 2_000
RETRY_MINUTES = 15


def safe_status(storage, status: str, detail: str) -> None:
    try:
        storage.source_status(status, detail, source="universe")
    except Exception as exc:  # noqa: BLE001 - health reporting must not terminate the worker
        log.error("Universe status write failed: %s", type(exc).__name__)


def sync_once(config, storage, resolver) -> list[dict]:
    """Resolve fully, then publish only validated membership."""
    provider = config.provider
    handle = storage.start_ingestion_run(
        pipeline="us_universe_sync", market_code="USA", source=provider,
        universe=config.name, requested_series=len(config.indexes),
        metadata={"provider": provider, "indexes": [
            getattr(item, "name", getattr(item, "symbol", "")) for item in config.indexes]},
    )
    try:
        if getattr(config, "version", None) == 1:  # compatibility for old callers only
            master, resolved = legacy_resolve(config, resolver)
            previous = storage.active_reference_count()
            if len(master) < MINIMUM_MASTER_SIZE:
                raise ValueError(f"US master rejected: only {len(master)} eligible instruments")
            if previous and len(master) < int(previous * 0.8):
                raise ValueError(f"US master rejected: {len(master)} is below 80% of {previous}")
            lookup = storage.sync_reference_master(
                master, getattr(resolver, "last_exchange_raw_id", None))
            series = [{"instrument_id": lookup[item["symbol"]], **item} for item in resolved]
            storage.sync_expected_series(
                series, source="eodhd", universe=config.name, resolution="daily")
        else:
            universe = resolver.resolve(config.request())
            if isinstance(getattr(handle, "metadata", None), dict):
                handle.metadata["provenance"] = universe.provenance
            lookup = storage.upsert_resolved_constituents(
                universe.constituents, source="schwab")
            series = [{"instrument_id": lookup[item.symbol], **item.model_dump(),
                       "provider_symbol": item.symbol}
                      for item in universe.constituents]
            storage.sync_expected_series(
                series, source=config.daily_source, universe=config.name, resolution="daily")
            storage.deactivate_expected_series(source="eodhd", resolution="daily")
        storage.finish_ingestion_run(
            handle, status="success", successful_series=len(series), rows_written=len(series))
        safe_status(storage, "ready", f"{provider}: {len(series)} configured US stocks resolved")
        log.info("Published %d configured US stocks", len(series))
        return series
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        storage.finish_ingestion_run(
            handle, status="failed", failed_series=max(1, len(config.indexes)),
            error=detail[:300])
        safe_status(storage, "error", f"Universe refresh failed: {detail[:240]}")
        raise


def run_daemon(config, storage, resolver=None) -> int:
    resolver = resolver or create_resolver(config, storage)
    next_refresh = datetime.min.replace(tzinfo=UTC)
    last_heartbeat = datetime.min.replace(tzinfo=UTC)
    last_result: tuple[str, str] = (
        "error", f"{config.provider}: universe resolver has not completed")
    while not stop.is_set():
        now = datetime.now(UTC)
        if now >= next_refresh:
            try:
                series = sync_once(config, storage, resolver)
                last_result = (
                    "ready", f"{config.provider}: {len(series)} configured US stocks resolved")
                next_refresh = now + timedelta(minutes=config.refresh_interval_minutes)
            except Exception as exc:  # noqa: BLE001 - daemon retries all provider/storage failures
                log.error("Universe refresh failed: %s", type(exc).__name__)
                last_result = (
                    "error", f"{config.provider}: universe refresh failed: {type(exc).__name__}")
                next_refresh = now + timedelta(minutes=RETRY_MINUTES)
            last_heartbeat = now
        elif (now - last_heartbeat).total_seconds() >= 60:
            safe_status(storage, *last_result)
            last_heartbeat = now
        stop.wait(1)
    safe_status(storage, "stopped", "Universe resolver stopped gracefully")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to the non-secret universe YAML")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Resolve and publish once, then exit")
    mode.add_argument("--daemon", action="store_true", help="Refresh continuously")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001 - argparse should report all config parse failures
        log.error("Invalid universe config: %s", exc)
        return 2
    storage = USStorage.from_environment()
    resolver = create_resolver(config, storage)
    if args.daemon:
        return run_daemon(config, storage, resolver)
    try:
        sync_once(config, storage, resolver)
        return 0
    except Exception:  # noqa: BLE001 - sync_once already records sanitized failure health
        return 1


if __name__ == "__main__":
    sys.exit(main())
