"""Compute FactorLab's US sector and composite indexes (ADR 0017).

Every run replays the whole history from the catalogue's base date in memory (the
engine is deterministic and streams closes a year at a time), then writes only the
trailing ``--window-days`` of levels and weights, or everything with ``--rebuild``.
Late bars and reclassifications therefore heal themselves within the window, and the
newest day is rewritten until its coverage passes the gate.

``--daemon`` runs once at start and then every weekday after ``--after`` (UTC), when
the collector's post-close daily pass has normally finished.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from uuid import UUID

from clickhouse_connect.driver.exceptions import ClickHouseError

from factorlab.core.logging import configure_logging, redact
from factorlab.indexes import (
    METHODOLOGY_VERSION,
    IndexCatalog,
    IndexDay,
    IndexEngine,
    Observations,
    Span,
    Timeline,
    load_catalog,
)
from factorlab.runtime.monitor import MonitoredEvent, monitor_client
from factorlab.storage.clickhouse import _version
from factorlab.storage.indexes import IndexStorage
from factorlab.storage.v2_us import V2USStorage

log = logging.getLogger("factorlab.us-indexes")
stop = MonitoredEvent()
FLUSH_ROWS = 200_000
JOB_ERRORS = (ClickHouseError, OSError, RuntimeError, ValueError)


class IndexStore(Protocol):
    """What the runner needs from ``IndexStorage`` (tests substitute an in-memory store)."""

    def universe_spans(self, universe: str) -> list[tuple[UUID, date, date | None]]: ...
    def sector_spans(self, classification: str) -> list[tuple[UUID, str, date, date | None]]: ...
    def shares(self, listings: Sequence[UUID]) -> dict[UUID, list[tuple[date, float]]]: ...
    def closes(
        self, listings: Sequence[UUID], start: date, end: date, *, source: str
    ) -> Iterator[tuple[date, dict[UUID, float]]]: ...
    def start_computation(
        self, *, dataset: str, code_version: str, parameters: dict[str, Any]
    ) -> UUID: ...
    def finish_computation(
        self, *, input_rows: int, rows: int, status: str, error: str | None = None
    ) -> None: ...
    def write_levels(self, rows: Sequence[dict[str, Any]]) -> None: ...
    def write_weights(self, rows: Sequence[dict[str, Any]]) -> None: ...


def members_on(universe: Timeline[None], sectors: Timeline[str]) -> Callable[[date], dict]:
    """Universe members with their sector on a day, memoised for the current day."""
    cache: dict[date, dict[UUID, str | None]] = {}

    def on(day: date) -> dict[UUID, str | None]:
        if day not in cache:
            cache.clear()
            classified = sectors.on(day)
            cache[day] = {lid: classified.get(lid) for lid in universe.on(day)}
        return cache[day]

    return on


def _rows(result: IndexDay, computation_id: UUID, now: datetime) -> tuple[dict, list[dict]]:
    version = _version(now)
    level = {
        "index_code": result.code,
        "trade_date": result.trade_date,
        "level": result.level,
        "ret_1d": result.ret,
        "n_constituents": result.n_constituents,
        "coverage": result.coverage,
        "complete": result.complete,
        "methodology_version": METHODOLOGY_VERSION,
        "computation_id": computation_id,
        "computed_at": now,
        "version": version,
    }
    weights = [
        {
            "index_code": result.code,
            "trade_date": result.trade_date,
            "listing_id": c.listing,
            "weight": c.weight,
            "ret_1d": c.ret,
            "contribution": c.contribution,
            "computation_id": computation_id,
            "version": version,
        }
        for c in result.constituents
    ]
    return level, weights


def run_once(
    store: IndexStore,
    catalog: IndexCatalog,
    *,
    source: str,
    today: date,
    window_days: int = 14,
    rebuild: bool = False,
    code_version: str = "dev",
) -> dict[str, Any]:
    universe = Timeline(
        [Span(lid, None, start, end) for lid, start, end in store.universe_spans(catalog.universe)]
    )
    sectors = Timeline(
        [
            Span(lid, sector, start, end)
            for lid, sector, start, end in store.sector_spans(catalog.classification)
        ]
    )
    listings = sorted({span.listing for span in universe.spans}, key=str)
    if not listings:
        raise ValueError(f"universe {catalog.universe} has no published members")
    shares = Observations(store.shares(listings))
    engine = IndexEngine(
        catalog.definitions(),
        base_date=catalog.base_date,
        members=members_on(universe, sectors),
        shares=shares.on,
    )
    write_from = catalog.base_date if rebuild else today - timedelta(days=window_days)
    computation_id = store.start_computation(
        dataset="derived.index_levels",
        code_version=code_version,
        parameters={
            "catalog": catalog.model_dump(mode="json"),
            "source": source,
            "write_from": write_from,
            "methodology_version": METHODOLOGY_VERSION,
        },
    )
    now = datetime.now(UTC)
    levels: list[dict] = []
    weights: list[dict] = []
    written = days = 0
    latest: Mapping[str, IndexDay] = {}
    try:
        warm = catalog.base_date - timedelta(days=10)  # a prior close for the first return
        for day, closes in store.closes(listings, warm, today, source=source):
            results = engine.step(day, closes)
            days += 1
            if results:
                latest = {r.code: r for r in results}
            if day < write_from:
                continue
            for result in results:
                level, rows = _rows(result, computation_id, now)
                levels.append(level)
                weights.extend(rows)
            if len(weights) >= FLUSH_ROWS:
                store.write_levels(levels)
                store.write_weights(weights)
                written += len(levels) + len(weights)
                levels, weights = [], []
        store.write_levels(levels)
        store.write_weights(weights)
        written += len(levels) + len(weights)
    except JOB_ERRORS as exc:
        store.finish_computation(
            input_rows=days, rows=written, status="failed", error=redact(str(exc))[:240]
        )
        raise
    store.finish_computation(input_rows=days, rows=written, status="success")
    incomplete = sorted(code for code, r in latest.items() if not r.complete)
    return {
        "computation_id": str(computation_id),
        "sessions": days,
        "rows_written": written,
        "latest": max((r.trade_date for r in latest.values()), default=None),
        "incomplete": incomplete,
    }


def next_run(now: datetime, after: time) -> datetime:
    candidate = datetime.combine(now.date(), after, tzinfo=UTC)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factorlab-ingest-us indexes", description=__doc__)
    parser.add_argument(
        "--catalog", help="index catalogue YAML (default: the packaged catalog_us.yaml)"
    )
    parser.add_argument("--source", default="schwab", help="market.bars source for closes")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--rebuild", action="store_true", help="write the full history")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--after", default="23:30", help="daemon run time, HH:MM UTC")
    args = parser.parse_args(argv)
    configure_logging(component="ingest-us", service="indexes-us")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    catalog = load_catalog(args.catalog)
    after = time.fromisoformat(args.after)
    storage = V2USStorage.from_environment()
    store = IndexStorage(storage.client)
    rebuild = args.rebuild
    try:
        while True:
            try:
                summary = run_once(
                    store,
                    catalog,
                    source=args.source,
                    today=datetime.now(UTC).date(),
                    window_days=args.window_days,
                    rebuild=rebuild,
                )
                log.info("Index run complete", extra={"summary": summary})
                rebuild = False
            except JOB_ERRORS as exc:
                log.error("Index run failed: %s", type(exc).__name__)
                monitor_client("indexes-us").error(
                    "index_run_failed", redact(str(exc))[:240], exception_type=type(exc).__name__
                )
                if not args.daemon:
                    return 1
            if not args.daemon:
                return 0
            wake = next_run(datetime.now(UTC), after)
            while not stop.is_set() and datetime.now(UTC) < wake:
                stop.wait(30)
            if stop.is_set():
                return 0
    finally:
        storage.client.close()


if __name__ == "__main__":
    sys.exit(main())
