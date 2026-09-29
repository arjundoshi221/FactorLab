"""Provider-agnostic ingestion runner (docs/architecture/07 §11).

Runs any binding from ``configs/ingestion/bindings.yaml`` through the engine.
It never imports a provider: the caller passes provider *names* and sources come
from the registry, so switching or adding a provider does not touch this module
(07 rule R4). Each ingest component exposes it as its ``engine`` subcommand with
the providers it ships; ``scripts/factlab_ingest.py`` runs it with all of them.

Usage:
    python scripts/factlab_ingest.py validate
    python scripts/factlab_ingest.py run --dataset ref.listings --market IND --dry-run
    python scripts/factlab_ingest.py run --dataset market.bars --market IND --resolution 1min \\
        --instrument "NSE_EQ|INE002A01018,RELIANCE,INE002A01018" --dry-run
    python scripts/factlab_ingest.py run --dataset market.bars --market IND --resolution 1min \\
        --listing <listing-uuid> [--lookback-minutes 30]
    python scripts/factlab_ingest.py run --dataset market.bars --market USA --resolution daily \
        --instance eodhd --universe sp500
    python scripts/factlab_ingest.py run --dataset ref.listings --market USA --instance schwab \
        --symbol BRK-B --dry-run
    python scripts/factlab_ingest.py replay --dataset market.bars --market IND \\
        --resolution 1min --raw-id <uuid>

``--dry-run`` swaps in the in-memory sink: the provider is called and every
record is validated, but nothing is written to ClickHouse.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from factorlab.core.logging import configure_logging
from factorlab.ingest.bindings import (
    Binding,
    BindingsFile,
    load_bindings,
    validate_bindings,
)
from factorlab.ingest.datasets import (
    BarRequest,
    InstrumentRef,
    ReferenceRequest,
    SeriesWindow,
    SnapshotRequest,
    dataset,
)
from factorlab.ingest.datasets.fundamentals import CompanyRef, CompanyRequest
from factorlab.ingest.datasets.political import FilingsRequest
from factorlab.ingest.engine import (
    bar_request,
    reference_request,
    replay,
    run_binding,
    source_priority_rows,
)
from factorlab.ingest.memory import InMemorySink
from factorlab.ingest.provider import RunSummary
from factorlab.ingest.registry import REGISTRY, load_providers, source_for
from factorlab.runtime import ExitCode

log = logging.getLogger("factlab_ingest")

_COUNTRY = {"IND": "IN", "USA": "US"}


def _load(args: argparse.Namespace) -> BindingsFile:
    load_providers(args.providers)
    bindings = load_bindings(Path(args.bindings) if args.bindings else None)
    bindings = bindings.for_providers(args.providers)
    validate_bindings(bindings, REGISTRY)
    return bindings


def _select(bindings: BindingsFile, args: argparse.Namespace) -> list[Binding]:
    chosen = [b for b in bindings.enabled(dataset=args.dataset, market=args.market,
                                          resolution=args.resolution)
              if args.instance is None or b.instance_name == args.instance]
    if not chosen:
        raise SystemExit(f"no enabled binding for {args.dataset} {args.market} "
                         f"{args.resolution or ''} {args.instance or ''}".strip())
    return chosen


def _sink(market: str, dry_run: bool) -> Any:
    if dry_run:
        return InMemorySink()
    from factorlab.storage.sinks import ClickHouseSinks

    return ClickHouseSinks.from_environment(market)


def _parse_instrument(text: str, alias_kind: str, market: str) -> InstrumentRef:
    alias, _, rest = text.partition(",")
    symbol, _, isin = rest.partition(",")
    exchange = {"IND": "NSE"}.get(market, "")  # US refs resolve by alias; no default venue
    return InstrumentRef(alias_kind, alias, exchange, symbol or alias, _COUNTRY[market],
                         isin=isin or None)


def _request(binding: Binding, source: Any, sink: Any, args: argparse.Namespace
             ) -> tuple[Any, tuple[str, ...]]:
    spec = dataset(binding.dataset)
    if spec.request_type is FilingsRequest:
        filings = sink.recent_filings(chamber=args.chamber, limit=args.recent_filings)
        return FilingsRequest(binding.market, tuple(filings), params=dict(binding.params)), ()
    if spec.request_type is CompanyRequest:
        companies = []
        for text in args.cik:
            cik, _, ticker = text.partition(":")
            companies.append(CompanyRef(cik, ticker or None))
        if not companies:
            raise SystemExit("fundamentals datasets need --cik <cik>[:<ticker>]")
        return CompanyRequest(binding.market, tuple(companies), params=dict(binding.params)), ()
    if spec.request_type is SnapshotRequest:
        return SnapshotRequest(binding.market, params=dict(binding.params)), ()
    if spec.request_type is ReferenceRequest and not spec.reference:
        return ReferenceRequest(binding.market, params=dict(binding.params)), ()
    if spec.reference:
        listings = [uuid.UUID(x) for x in args.listing]
        request = reference_request(binding, listings, reference=sink if listings else None)
        if args.symbol:
            params = {**request.params, "symbols": [*request.params.get("symbols", ()),
                                                    *args.symbol]}
            request = type(request)(market=request.market, params=params,
                                    instruments=request.instruments)
        return request, ()
    now = datetime.now(UTC)
    lookback = timedelta(minutes=args.lookback_minutes)
    if args.instrument:
        refs = [_parse_instrument(t, source.capabilities.alias_kind, binding.market)
                for t in args.instrument]
        return BarRequest(binding.market, binding.resolution or "",
                          tuple(SeriesWindow(r, now - lookback, now) for r in refs),
                          params=dict(binding.params)), ()
    listings = [uuid.UUID(x) for x in args.listing]
    if args.universe:
        listings += sink.universe_members(args.universe)
    for exchange in args.exchange:
        listings += sink.active_listings(exchange)
    if not listings:
        raise SystemExit("bar datasets need --universe <code>, --exchange <code>, --listing <uuid> "
                         "or --instrument <alias[,sym,isin]>")
    return bar_request(binding, source, list(dict.fromkeys(listings)), reference=sink,
                       checkpoints=sink, now=now, default_lookback=lookback)


def _report(summary: RunSummary) -> None:
    print(json.dumps({
        "run_id": str(summary.run_id), "source": summary.source, "pipeline": summary.pipeline,
        "status": summary.status, "rows_written": summary.rows_written,
        "failed_units": [{"name": u.name, "error": u.error} for u in summary.failed_units],
    }, indent=1))


def _exit(summaries: list[RunSummary]) -> int:
    statuses = {s.status for s in summaries}
    if statuses <= {"success"}:
        return int(ExitCode.OK)
    return int(ExitCode.FATAL if statuses == {"failed"} else ExitCode.WARN)


def cmd_validate(args: argparse.Namespace) -> int:
    bindings = _load(args)
    for b in bindings.bindings:
        print(f"{b.role:9} {b.pipeline:48} market={b.market} priority={b.priority}")
    return int(ExitCode.OK)


def cmd_sync_priorities(args: argparse.Namespace) -> int:
    """Write bindings' priorities to ref.source_priorities (read by market.bars_best)."""
    bindings = _load(args)
    rows = source_priority_rows(bindings.bindings)
    for row in rows:
        print(f"{row['role']:9} {row['dataset']:30} {row['country_code']} "
              f"{row['resolution'] or '-':6} {row['source']:20} priority={row['priority']}")
    if args.dry_run:
        return int(ExitCode.OK)
    for market in sorted({b.market for b in bindings.bindings}):
        country = {"IND": "IN", "USA": "US"}[market]
        sink = _sink(market, False)
        try:
            sink.sync_source_priorities([r for r in rows if r["country_code"] == country])
        finally:
            sink.close()
    return int(ExitCode.OK)


def cmd_run(args: argparse.Namespace) -> int:
    summaries = []
    for binding in _select(_load(args), args):
        sink = _sink(binding.market, args.dry_run)
        try:
            source = source_for(binding)
            request, unmapped = _request(binding, source, sink, args)
            summary = run_binding(binding, source, sink, request, unmapped=unmapped,
                                  metadata={"dry_run": args.dry_run})
        finally:
            close = getattr(sink, "close", None)
            if close:
                close()
        _report(summary)
        summaries.append(summary)
    return _exit(summaries)


MARKET_WINDOWS = {
    # calendar, open, close, tz: the session gate for --sessions-only daemons
    "IND": ("XBOM", time(9, 15), time(15, 30), ZoneInfo("Asia/Kolkata")),
    "USA": ("XNYS", time(9, 30), time(16, 0), ZoneInfo("America/New_York")),
}


def in_session(market: str, now: datetime | None = None, *, pre_open_min: int = 0,
               post_close_min: int = 0) -> bool:
    """True inside the market's session (plus buffers) on a trading day."""
    from factorlab.calendars.window import MarketWindow

    calendar, opens, closes, zone = MARKET_WINDOWS[market]
    window = MarketWindow(calendar_key=calendar, open_time=opens, close_time=closes, tz=zone,
                          pre_open_min=pre_open_min)
    local = (now or datetime.now(UTC)).astimezone(zone)
    if not window.is_trading_day(local.date()):
        return False
    return (window.poll_start(local.date()) <= local
            <= window.close_dt(local.date()) + timedelta(minutes=post_close_min))


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run ``run`` every --interval-seconds until SIGTERM (07 §11.2).

    This is what a Compose service runs after a provider's cutover, e.g.
    ``factlab_ingest.py daemon --dataset market.bars --market IND --resolution 1min
    --universe nifty500 --interval-seconds 60 --sessions-only``.
    """
    from factorlab.runtime import GracefulShutdown, Heartbeat

    heartbeat = Heartbeat(f"ingest-{args.dataset}-{args.market}".replace(".", "-"))
    with GracefulShutdown(log) as shutdown:
        while not shutdown.triggered:
            started = datetime.now(UTC)
            if args.sessions_only and not in_session(
                    args.market, started, pre_open_min=args.pre_open_minutes,
                    post_close_min=args.post_close_minutes):
                log.debug("outside the %s session; idle", args.market)
            else:
                try:
                    cmd_run(args)
                except SystemExit:
                    raise
                except Exception:
                    log.exception("ingestion cycle failed; retrying next interval")
            heartbeat.tick()
            deadline = started + timedelta(seconds=args.interval_seconds)
            while not shutdown.triggered and datetime.now(UTC) < deadline:
                shutdown_sleep(min(1.0, (deadline - datetime.now(UTC)).total_seconds()))
    return int(ExitCode.OK)


def shutdown_sleep(seconds: float) -> None:
    import time as _time

    if seconds > 0:
        _time.sleep(seconds)


def cmd_replay(args: argparse.Namespace) -> int:
    [binding] = _select(_load(args), args)[:1]
    sink = _sink(binding.market, False)
    try:
        captures = [sink.load_raw(uuid.UUID(raw_id)) for raw_id in args.raw_id]
        summary = replay(binding, source_for(binding), sink, captures)
    finally:
        sink.close()
    _report(summary)
    return _exit([summary])


def main(argv: list[str] | None = None, *, providers: Sequence[str],
         prog: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0])
    parser.set_defaults(providers=tuple(providers))
    parser.add_argument("--bindings", help="bindings YAML (default configs/ingestion/bindings.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="load and validate bindings against the registry")
    sync = commands.add_parser("sync-priorities",
                               help="write binding priorities to ref.source_priorities")
    sync.add_argument("--dry-run", action="store_true")
    for name in ("run", "daemon", "replay"):
        sub = commands.add_parser(name)
        sub.add_argument("--dataset", required=True)
        sub.add_argument("--market", required=True, choices=sorted(_COUNTRY))
        sub.add_argument("--resolution")
        sub.add_argument("--instance")
        if name in ("run", "daemon"):
            sub.add_argument("--dry-run", action="store_true")
            sub.add_argument("--listing", action="append", default=[])
            sub.add_argument("--universe", action="append", default=[],
                             help="bar datasets: fetch every current member of this universe")
            sub.add_argument("--exchange", action="append", default=[],
                             help="bar datasets: every active listing on this exchange")
            sub.add_argument("--instrument", action="append", default=[])
            sub.add_argument("--symbol", action="append", default=[],
                             help="per-symbol reference lookups (e.g. Schwab instruments)")
            sub.add_argument("--lookback-minutes", type=int, default=24 * 60)
            sub.add_argument("--recent-filings", type=int, default=20,
                             help="alt.political_trades: fetch the N newest filings")
            sub.add_argument("--chamber", default="house")
            sub.add_argument("--cik", action="append", default=[],
                             help="fundamentals: <cik>[:<ticker>] (the ticker resolves the issuer)")
        if name == "daemon":
            sub.add_argument("--interval-seconds", type=int, default=60)
            sub.add_argument("--sessions-only", action="store_true",
                             help="idle outside the market session (trading days only)")
            sub.add_argument("--pre-open-minutes", type=int, default=0)
            sub.add_argument("--post-close-minutes", type=int, default=5)
        if name == "replay":
            sub.add_argument("--raw-id", action="append", required=True)
    args = parser.parse_args(argv)
    configure_logging(level="DEBUG" if args.verbose else None)
    handler = {"validate": cmd_validate, "run": cmd_run, "daemon": cmd_daemon,
               "replay": cmd_replay,
               "sync-priorities": cmd_sync_priorities}[args.command]
    return handler(args)
