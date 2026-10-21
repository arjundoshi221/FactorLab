"""Resolve and publish the configured US equity collection universe."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml
from clickhouse_connect.driver.exceptions import ClickHouseError

from factorlab.components.ingest_us.engine_client import EngineClient
from factorlab.components.ingest_us.universe import UniverseConfig, load_config
from factorlab.components.ingest_us.universe.base import UniverseResolver
from factorlab.components.ingest_us.universe.engine import (
    RosterSink,
    create_resolver,
    stage_sectors,
)
from factorlab.core.logging import configure_logging, redact
from factorlab.ingest.datasets import ConstituentRecord, InstrumentRef
from factorlab.ingest.provider import RawCapture, ingestion_run
from factorlab.runtime.monitor import MonitoredEvent, monitor_client
from factorlab.sources.schwab.normalize import provider_symbol
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.sinks.identity import IdentityResolver
from factorlab.storage.v2_us import V2USStorage as USStorage

log = logging.getLogger("factorlab.us-universe")
stop = MonitoredEvent()
RETRY_MINUTES = 15
JOB_ERRORS = (ClickHouseError, OSError, RuntimeError, ValueError, yaml.YAMLError)


def failure_detail(exc: Exception) -> str:
    detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
    return redact(detail)[:240]


def safe_status(storage, status: str, detail: str) -> None:
    try:
        storage.source_status(status, detail, source="universe")
    except JOB_ERRORS as exc:
        log.error("Universe status write failed: %s", type(exc).__name__)


def snapshot_shares(storage: USStorage, client: EngineClient, symbols: list[str]) -> int:
    """Record today's shares outstanding for published equities; never fails the roster."""
    equities = [symbol for symbol in symbols if not symbol.startswith("$")]
    if not equities:
        return 0
    sink = ClickHouseSinks(storage)
    try:
        with ingestion_run(
            sink, pipeline="us_shares_outstanding", source="schwab", market_code="USA"
        ) as ctx:
            return client.collect_shares(ctx, sink, equities)
    except JOB_ERRORS as exc:
        log.warning("Shares outstanding snapshot failed: %s", failure_detail(exc))
        return 0


def sync_once(
    config: UniverseConfig,
    storage: USStorage,
    resolver: UniverseResolver | None = None,
    *,
    client: EngineClient | None = None,
) -> list[dict[str, Any]]:
    """Validate all references before publishing an expected collection roster."""
    owned = client is None
    client = client or EngineClient()
    sink = RosterSink(storage)
    current = resolver
    try:
        with ingestion_run(
            sink,
            pipeline="us_universe_sync",
            source=config.provider,
            market_code="USA",
            universe=config.name,
            metadata={"provider": config.provider, "indexes": [i.name for i in config.indexes]},
        ) as ctx:
            current = resolver or create_resolver(
                config, storage, client=client, ctx=ctx, sink=sink
            )
            try:
                universe = current.resolve(config.request())
                try:
                    stage_sectors(config, ctx, sink)
                except JOB_ERRORS as exc:
                    # Sectors are best effort: the roster still publishes without them.
                    log.warning("Sector refresh failed: %s", failure_detail(exc))
                    sink.sectors.clear()
                refs = [
                    InstrumentRef(
                        "schwab_symbol", provider_symbol(item.symbol), "", item.symbol, "US"
                    )
                    for item in universe.constituents
                ]
                identities = IdentityResolver(sink.client).listings(refs)
                if len(identities) != len(refs):
                    raise ValueError("validated roster contains unresolved canonical identities")
                # Archive the explicit union recipe, including extras and its input
                # captures, so generic engine jobs can use the configured universe.
                manifest = RawCapture(
                    body=json.dumps(
                        {
                            "config": config.model_dump(mode="json"),
                            "symbols": [ref.trading_symbol for ref in refs],
                            "provenance": universe.provenance,
                        },
                        sort_keys=True,
                        default=str,
                    ).encode(),
                    request_key=f"configured-universe:{config.name}",
                    transport="file",
                    fetched_at=datetime.now(UTC),
                    source_url=f"factorlab:configured-universe/{config.name}",
                    metadata={"capture_kind": "derived_union_manifest"},
                )
                raw_id = sink.archive_raw(
                    manifest, source=config.provider, source_channel="configured_universe:manifest"
                )
                sink.snapshots.append(
                    (
                        tuple(ConstituentRecord(config.name, ref) for ref in refs),
                        ctx.provenance(
                            raw_id=raw_id,
                            source_channel="configured_universe:manifest",
                            as_of_time=manifest.fetched_at,
                        ),
                    )
                )
                sink.publish(refs)
                series = [
                    {
                        "instrument_id": identities[ref].listing_id,
                        **item.model_dump(),
                        "provider_symbol": ref.alias_value,
                    }
                    for ref, item in zip(refs, universe.constituents, strict=True)
                ]
                storage.sync_expected_series(
                    series, source=config.daily_source, universe=config.name, resolution="daily"
                )
                storage.deactivate_expected_series(source="eodhd", resolution="daily")
                ctx.succeed_unit("publish", rows_written=len(series))
            except JOB_ERRORS as exc:
                ctx.fail_unit("publish", type(exc).__name__)
                raise
        safe_status(
            storage, "ready", f"{config.provider}: {len(series)} configured US stocks resolved"
        )
        if config.shares_outstanding:
            snapshot_shares(storage, client, [str(item["symbol"]) for item in series])
        return series
    except JOB_ERRORS as exc:
        safe_status(storage, "error", f"Universe refresh failed: {failure_detail(exc)}")
        raise
    finally:
        if resolver is None and current is not None:
            close = getattr(current, "close", None)
            if callable(close):
                close()
        if owned:
            client.close()


def run_daemon(config, storage, resolver=None) -> int:
    next_refresh = datetime.min.replace(tzinfo=UTC)
    last_heartbeat = datetime.min.replace(tzinfo=UTC)
    last_result: tuple[str, str] = (
        "error",
        f"{config.provider}: universe resolver has not completed",
    )
    while not stop.is_set():
        now = datetime.now(UTC)
        if now >= next_refresh:
            try:
                series = sync_once(config, storage, resolver)
                last_result = (
                    "ready",
                    f"{config.provider}: {len(series)} configured US stocks resolved",
                )
                next_refresh = now + timedelta(minutes=config.refresh_interval_minutes)
            except JOB_ERRORS as exc:
                log.error("Universe refresh failed: %s", type(exc).__name__)
                monitor_client("universe-us").error(
                    "universe_refresh_failed",
                    f"Universe refresh failed: {failure_detail(exc)}. Retrying in {RETRY_MINUTES} minutes.",
                    pipeline="us_universe_sync",
                    provider=config.provider,
                    retry_minutes=RETRY_MINUTES,
                    exception_type=type(exc).__name__,
                )
                last_result = (
                    "error",
                    f"{config.provider}: universe refresh failed: {failure_detail(exc)}",
                )
                next_refresh = now + timedelta(minutes=RETRY_MINUTES)
            last_heartbeat = now
        elif (now - last_heartbeat).total_seconds() >= 60:
            safe_status(storage, *last_result)
            last_heartbeat = now
        stop.wait(1)
    safe_status(storage, "stopped", "Universe resolver stopped gracefully")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to the non-secret universe YAML")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Resolve and publish once, then exit")
    mode.add_argument("--daemon", action="store_true", help="Refresh continuously")
    args = parser.parse_args(argv)
    configure_logging(component="ingest-us", service="universe-us")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        config = load_config(args.config)
    except JOB_ERRORS as exc:
        log.error("Invalid universe config: %s", exc)
        monitor_client("universe-us").error("universe_config_invalid", failure_detail(exc))
        return 2
    storage = USStorage.from_environment()
    resolver = None
    try:
        storage.seed_us_reference_data()
        if args.daemon:
            return run_daemon(config, storage, resolver)
        sync_once(config, storage, resolver)
        return 0
    except JOB_ERRORS as exc:
        monitor_client("universe-us").error(
            "universe_refresh_failed",
            f"Universe collection failed: {failure_detail(exc)}",
            pipeline="us_universe_sync",
            provider=config.provider,
        )
        return 1
    finally:
        storage.client.close()


if __name__ == "__main__":
    sys.exit(main())
