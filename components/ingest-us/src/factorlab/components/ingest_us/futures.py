"""Record US futures from Schwab ``/quotes`` (F-043).

Schwab REST has no futures price history, so this daemon builds one:

* once per Globex session it refreshes the roster: root listings, then the active
  and next contracts of each root (``us_futures_roster``);
* while Globex is open it snapshots every roster contract each ``--interval``
  seconds into ``market.futures_quotes`` (``us_futures_quotes``);
* when a session closes it writes one provisional daily bar per contract from the
  session's last snapshot, and an hour into the next session it rewrites the bar with
  close = the exchange settlement (``us_futures_daily``).

The roster is ``configs/universes/us_futures.yaml``. Only ``/marketdata/v1/quotes`` is
called; true intraday bars need streaming (F-021).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import yaml
from clickhouse_connect.driver.exceptions import ClickHouseError
from pydantic import BaseModel, ConfigDict, Field

from factorlab.calendars import globex
from factorlab.calendars.us import NY
from factorlab.components.ingest_us.settings import CollectionSettings
from factorlab.core import paths
from factorlab.core.logging import configure_logging, redact
from factorlab.ingest.datasets import ContractBarRecord, ContractRecord, WriteResult
from factorlab.ingest.errors import AuthRequired
from factorlab.ingest.provider import Provenance, ingestion_run
from factorlab.runtime.monitor import MonitoredEvent, monitor_client
from factorlab.sources.schwab.futures import FuturesRoot, futures_ref, parse_contract
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.sinks.identity import IdentityResolver

log = logging.getLogger("factorlab.us-futures")
stop = MonitoredEvent()
SOURCE = "schwab"
STATUS_SOURCE = "schwab_futures"
CHANNEL = "schwab:quotes"
SETTLEMENT_DELAY = timedelta(hours=1)  # into the next session before settlements are trusted
CLOSED_RECHECK = timedelta(minutes=15)
JOB_ERRORS = (AuthRequired, ClickHouseError, OSError, RuntimeError, ValueError)


class Roster(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(1, ge=1, le=1)
    roots: list[dict[str, Any]] = Field(min_length=1)

    def entries(self) -> list[FuturesRoot]:
        entries = [FuturesRoot.from_mapping(item) for item in self.roots]
        names = [entry.root for entry in entries]
        if len(set(names)) != len(names):
            raise ValueError("futures roster lists a root twice")
        return entries


def load_roster(path: Path | None = None) -> list[FuturesRoot]:
    path = path or paths.config_dir() / "universes" / "us_futures.yaml"
    return Roster.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).entries()


class RosterSink(ClickHouseSinks):
    """Remember which written contracts resolve, so the poller quotes only real ones."""

    def __init__(self, storage: Any) -> None:
        super().__init__(storage)
        self.roster: dict[str, str] = {}

    def upsert_contracts(
        self, rows: Sequence[ContractRecord], *, provenance: Provenance, mode: Any = "authoritative"
    ) -> WriteResult:
        result = super().upsert_contracts(rows, provenance=provenance, mode=mode)
        resolved = IdentityResolver(self.client).contracts(row.ref for row in rows)
        for ref in resolved:
            self.roster[ref.alias_value] = ref.exchange_code
        return result


class FuturesStorage(Protocol):
    def futures_session_closes(self, day: date, *, source: str) -> list[dict[str, Any]]: ...
    def futures_settlements(
        self, day: date, *, after: datetime, source: str
    ) -> dict[UUID, Any]: ...
    def source_status(self, status: str, detail: str, *, source: str = ...) -> None: ...


class FuturesClient(Protocol):
    def collect_futures_roster(
        self, ctx: Any, sink: Any, roots: Sequence[Mapping[str, Any]], as_of: date
    ) -> Any: ...
    def collect_futures_quotes(self, ctx: Any, sink: Any, contracts: Mapping[str, str]) -> Any: ...


def last_closed_session(now: datetime) -> date:
    """The trade date of the most recent Globex session that has closed by ``now``."""
    if not globex.is_open(now):
        return globex.session_date(now)
    local = now.astimezone(NY)
    opened = datetime.combine(local.date(), globex.OPEN, tzinfo=NY)
    if opened > local:
        opened -= timedelta(days=1)
    return globex.session_date(opened - timedelta(seconds=1))


def settlement_after(day: date) -> datetime:
    """When the settlement of session ``day`` can be trusted in quotes."""
    return globex.next_open(globex.session_close(day)) + SETTLEMENT_DELAY


def daily_bar(
    row: Mapping[str, Any], day: date, mic: str, settlement: Decimal | None
) -> ContractBarRecord | None:
    """One session's bar from its last snapshot; close is the settlement when it fits."""
    o, h, low, last = (row.get(k) for k in ("day_open", "day_high", "day_low", "last_price"))
    close = last
    if settlement is not None and (low is None or h is None or low <= settlement <= h):
        close = settlement
    if close is None:
        return None
    volume, oi = row.get("day_volume"), row.get("oi")
    try:
        return ContractBarRecord(
            futures_ref(str(row["source_symbol"]), mic),
            "daily",
            datetime.combine(day, time(12), tzinfo=NY).astimezone(UTC),
            o,
            h,
            low,
            close,
            volume=None if volume is None else int(volume),
            oi=None if oi is None else int(oi),
            settlement_price=settlement,
            session="regular",
        )
    except ValueError as exc:
        log.warning("Skipping inconsistent %s bar for %s: %s", row["source_symbol"], day, exc)
        return None


class FuturesRecorder:
    def __init__(
        self,
        storage: FuturesStorage,
        client: FuturesClient,
        sink: Any,
        roots: Sequence[FuturesRoot],
        *,
        interval: timedelta = timedelta(seconds=60),
    ) -> None:
        self.storage = storage
        self.client = client
        self.sink = sink
        self.roots = list(roots)
        self.venues = {root.root: root.mic for root in self.roots}
        self.interval = interval
        self.contracts: dict[str, str] = {}
        self.roster_day: date | None = None
        self.provisional: set[date] = set()
        self.final: set[date] = set()

    # -- steps -------------------------------------------------------------------
    def refresh_roster(self, now: datetime) -> None:
        with ingestion_run(
            self.sink, pipeline="us_futures_roster", source=SOURCE, market_code="USA"
        ) as ctx:
            self.sink.roster = {}
            self.client.collect_futures_roster(
                ctx,
                self.sink,
                [root.to_mapping() for root in self.roots],
                now.astimezone(NY).date(),
            )
        found = dict(self.sink.roster)
        if not found:
            raise RuntimeError("futures roster refresh resolved no contracts")
        self.contracts = found
        self.roster_day = globex.session_date(now)
        log.info("Futures roster refreshed", extra={"contracts": sorted(found)})

    def poll_quotes(self) -> None:
        with ingestion_run(
            self.sink,
            pipeline="us_futures_quotes",
            source=SOURCE,
            market_code="USA",
            requested_series=len(self.contracts),
        ) as ctx:
            self.client.collect_futures_quotes(ctx, self.sink, self.contracts)

    def _venue(self, symbol: str) -> str:
        parsed = parse_contract(symbol)
        return self.venues.get(parsed[0] if parsed else symbol, "")

    def write_daily_bars(self, day: date, now: datetime, *, final: bool) -> int:
        closes = self.storage.futures_session_closes(day, source=SOURCE)
        settlements = (
            self.storage.futures_settlements(day, after=settlement_after(day), source=SOURCE)
            if final
            else {}
        )
        groups: dict[Any, list[ContractBarRecord]] = {}
        for row in closes:
            bar = daily_bar(
                row,
                day,
                self._venue(str(row["source_symbol"])),
                settlements.get(row["contract_id"]),
            )
            if bar is not None:
                groups.setdefault(row.get("raw_id"), []).append(bar)
        if not groups:
            log.info("No futures quotes recorded for session %s", day.isoformat())
            return 0
        written = 0
        with ingestion_run(
            self.sink, pipeline="us_futures_daily", source=SOURCE, market_code="USA"
        ) as ctx:
            for raw_id, bars in groups.items():
                provenance = ctx.provenance(source_channel=CHANNEL, raw_id=raw_id, as_of_time=now)
                result = self.sink.write_contract_bars(bars, provenance=provenance)
                kind = "final" if final else "provisional"
                ctx.succeed_unit(f"{kind}:{day.isoformat()}:{raw_id}", result.rows_written)
                written += result.rows_written
        log.info(
            "Futures daily bars written",
            extra={"trade_date": day.isoformat(), "final": final, "rows": written},
        )
        return written

    def tick(self, now: datetime) -> datetime:
        """Do what is due at ``now``; return when to wake next."""
        if self.roster_day != globex.session_date(now) or not self.contracts:
            self.refresh_roster(now)
        closed = last_closed_session(now)
        if not globex.is_open(now):
            if closed not in self.provisional:
                self.write_daily_bars(closed, now, final=False)
                self.provisional.add(closed)
            return min(globex.next_open(now), now + CLOSED_RECHECK)
        self.poll_quotes()
        if closed not in self.final and now >= settlement_after(closed):
            self.write_daily_bars(closed, now, final=True)
            self.final.add(closed)
        minute = now.replace(second=0, microsecond=0)
        return minute + self.interval + timedelta(seconds=5)


def main(argv: list[str] | None = None) -> int:
    import fcntl  # Production runs on Linux; import here keeps testable functions portable.

    from factorlab.components.ingest_us.engine_client import EngineClient
    from factorlab.storage.v2_us import V2USStorage

    parser = argparse.ArgumentParser(prog="factorlab-ingest-us futures", description=__doc__)
    parser.add_argument("--config", type=Path, help="roster YAML (default: us_futures.yaml)")
    parser.add_argument("--interval", type=int, default=60, help="seconds between quote polls")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    args = parser.parse_args(argv)
    if args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    configure_logging(component="ingest-us", service="futures-us")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    roots = load_roster(args.config)
    lock_path = CollectionSettings().futures_lock
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Another US futures recorder holds the lock")
            return 2
        storage = V2USStorage.from_environment()
        client = EngineClient()
        try:
            storage.seed_us_reference_data()
            recorder = FuturesRecorder(
                storage,
                client,
                RosterSink(storage),
                roots,
                interval=timedelta(seconds=args.interval),
            )
            while not stop.is_set():
                now = datetime.now(UTC)
                try:
                    wake = recorder.tick(now)
                    storage.source_status(
                        "ok" if globex.is_open(now) else "closed",
                        f"{len(recorder.contracts)} contracts",
                        source=STATUS_SOURCE,
                    )
                except JOB_ERRORS as exc:
                    log.error(
                        "Futures tick failed: %s: %s", type(exc).__name__, redact(str(exc))[:240]
                    )
                    monitor_client("futures-us").error(
                        "futures_tick_failed",
                        redact(str(exc))[:240],
                        exception_type=type(exc).__name__,
                    )
                    storage.source_status("error", type(exc).__name__, source=STATUS_SOURCE)
                    if args.once:
                        return 1
                    wake = now + timedelta(seconds=60)
                if args.once:
                    return 0
                while not stop.is_set() and datetime.now(UTC) < wake:
                    stop.wait(min(30.0, max((wake - datetime.now(UTC)).total_seconds(), 0.1)))
            storage.source_status("stopped", "signal", source=STATUS_SOURCE)
            return 0
        finally:
            client.close()
            storage.client.close()


if __name__ == "__main__":
    sys.exit(main())
