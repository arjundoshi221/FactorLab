"""Production US price collection. One process owns live polling and resumable backfill."""

from __future__ import annotations

import argparse
import logging
import math
import signal
import sys
from datetime import UTC, datetime, timedelta

import yaml
from clickhouse_connect.driver.exceptions import ClickHouseError

from factorlab.calendars.us import NY, bounds, latest_completed
from factorlab.components.ingest_us.engine_client import EngineClient, PriceSink
from factorlab.components.ingest_us.settings import CollectionSettings
from factorlab.core import paths
from factorlab.core.logging import configure_logging
from factorlab.ingest.datasets import BarRequest, SeriesWindow
from factorlab.ingest.engine import collect_binding
from factorlab.ingest.errors import AuthRequired
from factorlab.ingest.provider import ingestion_run
from factorlab.runtime.monitor import MonitoredEvent
from factorlab.sources.schwab.normalize import provider_symbol as schwab_symbol
from factorlab.storage.v2_us import REWRITE_DAYS
from factorlab.storage.v2_us import V2USStorage as USStorage

log = logging.getLogger("factorlab.schwab")
stop = MonitoredEvent()
FULL_UNIVERSE = "us_listed_equities"
MINUTE_UNIVERSE = "us_liquid_250"
MINUTE_TIER_SIZE = CollectionSettings().minute_tier_size
# Live sweeps start on a fixed cadence measured from the previous sweep's start.
LIVE_INTERVAL = timedelta(seconds=CollectionSettings().live_interval_seconds)
RETRY_BASE = timedelta(minutes=15)
RETRY_CAP = timedelta(hours=24)
COLLECTION_ERRORS = (ClickHouseError, OSError, RuntimeError, ValueError)


def retry_delay(attempts):
    """Exponential recovery backoff: 15m, 30m, 1h, ... capped at 24h."""
    return min(RETRY_BASE * 2 ** min(attempts, 7), RETRY_CAP)


def schedule_retry(retry_at, attempts, key, now):
    retry_at[key] = now + retry_delay(attempts.get(key, 0))
    attempts[key] = attempts.get(key, 0) + 1


def universe(name):
    config = yaml.safe_load((paths.config_dir() / "universes" / "us.yaml").read_text())
    symbols = config["universes"][name]["symbols"]
    return [(s.removesuffix(".US"), s.removesuffix(".US").replace("-", "/")) for s in symbols]


def session_bounds(day):
    session = bounds(day)
    if session is None:
        raise ValueError(f"No regular US trading session on {day}")
    return session


def collect(
    storage, client: EngineClient, item, resolution, *, now, live=False, universe_name="pilot"
):
    symbol, provider, instrument_id = item
    state = storage.state(instrument_id, resolution)
    state["symbol"] = symbol
    full = (
        not state["history_complete"]
        or not state["full_refreshed_at"]
        or now - state["full_refreshed_at"] >= timedelta(days=7)
    )
    end = now if live else session_bounds(latest_completed(now))[1]
    if live:
        opened = session_bounds(now.astimezone(NY).date())[0]
        start = (
            max(opened, state["last_bar"] - timedelta(minutes=2))
            if state.get("last_bar")
            else opened
        )
    elif full:
        start = (
            datetime(1970, 1, 1, tzinfo=UTC) if resolution == "daily" else now - timedelta(days=60)
        )
    else:
        start = state["checked_through"] - timedelta(days=7)
        if state.get("error") and (
            not str(state["error"]).startswith("Invalid Schwab candles:")
            or "Coverage incomplete:" in str(state["error"])
        ):
            gap = storage.gap_start(instrument_id, resolution, now)
            if gap:
                start = min(start, gap)
        if resolution == "1min":
            start = max(start, now - timedelta(days=60))
    binding = client.bindings["market.bars", resolution]
    if not live and resolution == "daily":
        # The weekly refresh re-reads history since 1970 to catch vendor corrections, and a
        # retained coverage gap re-reads from that gap on every recovery; write only the bars
        # that changed, or every listing rewrites up to ~680 monthly partitions each time.
        sink = PriceSink(
            storage,
            existing=storage.stored_daily_bars(
                instrument_id, source=binding.source_name, start=start, end=end
            ),
            rewrite_from=end - timedelta(days=REWRITE_DAYS),
        )
    else:
        sink = PriceSink(storage)
    pipeline = "us_live" if live else f"us_recovery_{resolution}"
    fetch_units = (
        1
        if resolution == "daily"
        else math.ceil(
            (
                end - max(start, end - timedelta(days=client.settings.minute_max_lookback_days))
            ).total_seconds()
            / (client.settings.minute_chunk_days * 86400)
        )
    )
    try:
        with ingestion_run(
            sink,
            pipeline=pipeline,
            source=binding.source_name,
            market_code="USA",
            universe=universe_name,
            requested_series=fetch_units + 1,
            metadata={
                "symbol": symbol,
                "resolution": resolution,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "full": full and not live,
                "expected_series": 1,
            },
        ) as ctx:
            mapped = sink.aliases_for([instrument_id], alias_kind="schwab_symbol")
            if instrument_id not in mapped:
                raise ValueError("configured listing has no Schwab alias; refresh its universe")
            ref = mapped[instrument_id]
            if ref.alias_value != provider:
                raise ValueError(
                    "configured provider symbol differs from the canonical Schwab alias"
                )
            request = BarRequest(
                "USA",
                resolution,
                (SeriesWindow(ref, start, end),),
                params={**binding.params, "require_complete": True},
            )
            outcomes = collect_binding(
                ctx,
                binding,
                client.bars_source(),
                sink,
                request,
                should_stop=stop.is_set,
                sleep=client.wait,
            )
            failures = [u for u in outcomes if not u.ok]
            auth = next(
                (u.error for u in failures if u.error and u.error.startswith("AuthRequired:")), None
            )
            if auth:
                raise AuthRequired(auth)
            if not sink.bars:
                ctx.fail_unit("coverage", "No finalized bars available to validate coverage")
                raise ValueError("No regular-session candles returned; coverage remains unresolved")
            first = min(r.bar_time for r in sink.bars)
            last = max(r.bar_time for r in sink.bars)
            state["available_from"] = min(state["available_from"] or first, first)
            state["last_bar"] = max(state["last_bar"] or last, last)
            missing = (
                storage.coverage(
                    instrument_id, symbol, resolution, start, end, state["available_from"]
                )
                or 0
            )
            errors = [u.error for u in failures if u.error]
            fetch_failures = [error for error in errors if "Invalid Schwab candles:" not in error]
            invalid_errors = [
                error[error.index("Invalid Schwab candles:") :]
                for error in errors
                if "Invalid Schwab candles:" in error
            ]
            if (
                not invalid_errors
                and not full
                and str(state.get("error") or "").startswith("Invalid Schwab candles:")
            ):
                invalid_errors.append(str(state["error"]))
            coverage_error = f"Coverage incomplete: {missing} missing bars" if missing else None
            state["error"] = (
                "; ".join(
                    [
                        *invalid_errors,
                        *fetch_failures,
                        *([coverage_error] if coverage_error else []),
                    ]
                )[:300]
                or None
            )
            if not live and not fetch_failures:
                state.update(history_complete=True, checked_through=end)
                if full:
                    state["full_refreshed_at"] = now
            storage.save_state(state)
            if coverage_error:
                ctx.fail_unit("coverage", coverage_error)
            elif state["error"] and not failures:
                ctx.fail_unit("retained_validation_error", state["error"])
            else:
                ctx.succeed_unit("coverage", rows_written=0)
            log.info(
                "%s %s: %d rows through %s", symbol, resolution, ctx.rows_written, last.isoformat()
            )
        return not missing and not fetch_failures
    except COLLECTION_ERRORS as exc:
        detail = (
            str(exc)
            if isinstance(exc, (ValueError, AuthRequired, RuntimeError))
            else type(exc).__name__
        )
        state["error"] = detail[:300]
        storage.save_state(state)
        raise


def safe_source_status(storage, source, status, detail):
    try:
        storage.source_status(status, detail, source=source)
    except COLLECTION_ERRORS as exc:
        log.error("Source status write failed for %s: %s", source, type(exc).__name__)


def pending_daily(items, states, target_day):
    """Return instruments needing history or a per-symbol daily fallback."""
    target = session_bounds(target_day)[1]
    pending = []
    for item in items:
        state = states.get(item["instrument_id"], {})
        error = state.get("error")
        if (
            not state.get("history_complete")
            or not state.get("checked_through")
            or state["checked_through"] < target
            or not state.get("full_refreshed_at")
            or state["full_refreshed_at"] <= target - timedelta(days=7)
            or (
                error
                and (
                    not str(error).startswith("Invalid Schwab candles:")
                    or "Coverage incomplete:" in str(error)
                )
            )
        ):
            pending.append(item)
    return pending


def collection_status(storage, pending, items):
    if pending:
        return "recovering", f"{len(pending)} daily histories pending"
    unresolved = storage.unresolved_series(source="schwab")
    if unresolved:
        return "incomplete", f"{unresolved} series have unresolved errors or gaps"
    return "ready", f"{len(items)} configured equities"


def extend_pending(pending, additions):
    queued = {item["instrument_id"] for item in pending}
    pending.extend(item for item in additions if item["instrument_id"] not in queued)


def select_minute_tier(storage, client, items):
    by_id = {item["instrument_id"]: item for item in items}
    selected = []
    target = min(MINUTE_TIER_SIZE, len(items))
    for candidate in storage.liquid_candidates(limit=target + 150) if target else []:
        item = by_id.get(candidate["instrument_id"])
        if item is None:
            continue
        provider = schwab_symbol(item["symbol"])
        selected.append({**item, "provider_symbol": provider})
        if len(selected) == target:
            break
    storage.sync_expected_series(
        selected, source="schwab", universe=MINUTE_UNIVERSE, resolution="1min"
    )
    return [(item["symbol"], item["provider_symbol"], item["instrument_id"]) for item in selected]


def run_full(args, storage, schwab: EngineClient):
    safe_source_status(
        storage,
        "schwab",
        "ready" if schwab.token_ready() else "auth_required",
        "Schwab token available"
        if schwab.token_ready()
        else "Authenticate Schwab on the broker-auth page",
    )
    items = []
    pending = []
    minute_items = []
    minute_pending = []
    next_live = datetime.min.replace(tzinfo=UTC)
    ranked_week = None
    next_rank_attempt = datetime.min.replace(tzinfo=UTC)
    daily_target = None
    last_heartbeat = datetime.min.replace(tzinfo=UTC)
    next_membership_poll = datetime.min.replace(tzinfo=UTC)
    retry_at = {}
    minute_retry_at = {}
    retry_attempts = {}
    minute_retry_attempts = {}
    while not stop.is_set():
        now = datetime.now(UTC)
        local_day = now.astimezone(NY).date()
        if now >= next_membership_poll:
            published = storage.active_expected_series(source="schwab", resolution="daily")
            old_ids = {item["instrument_id"] for item in items}
            new_ids = {item["instrument_id"] for item in published}
            if new_ids != old_ids:
                added = len(new_ids - old_ids)
                removed = len(old_ids - new_ids)
                items = published
                states = storage.states("daily", source="schwab") if items else {}
                pending = pending_daily(items, states, latest_completed(now)) if items else []
                retry_at = {key: value for key, value in retry_at.items() if key in new_ids}
                minute_retry_at = {
                    key: value for key, value in minute_retry_at.items() if key in new_ids
                }
                retry_attempts = {
                    key: value for key, value in retry_attempts.items() if key in new_ids
                }
                minute_retry_attempts = {
                    key: value for key, value in minute_retry_attempts.items() if key in new_ids
                }
                minute_items = [item for item in minute_items if item[2] in new_ids]
                minute_pending = [item for item in minute_pending if item[2] in new_ids]
                if removed:
                    retained = [
                        item
                        for item in storage.active_expected_series(
                            source="schwab", resolution="1min"
                        )
                        if item["instrument_id"] in new_ids
                    ]
                    storage.sync_expected_series(
                        retained, source="schwab", universe=MINUTE_UNIVERSE, resolution="1min"
                    )
                    minute_items = [
                        (item["symbol"], item["provider_symbol"], item["instrument_id"])
                        for item in retained
                    ]
                # Force daily recovery and minute ranking to account for changes now.
                daily_target = None
                ranked_week = None
                next_rank_attempt = datetime.min.replace(tzinfo=UTC)
                log.info(
                    "Loaded configured universe: %d stocks (%d added, %d removed)",
                    len(items),
                    added,
                    removed,
                )
            next_membership_poll = now + timedelta(seconds=60)
        if not items:
            safe_source_status(
                storage,
                "schwab",
                "waiting",
                "Waiting for universe-us to publish a configured universe",
            )
            if not args.daemon:
                return 1
            stop.wait(30)
            continue
        if not schwab.token_ready(now) and not args.daemon:
            safe_source_status(
                storage, "schwab", "auth_required", "Authenticate Schwab on the broker-auth page"
            )
            return 1
        target_day = latest_completed(now)
        if daily_target != target_day:
            states = storage.states("daily", source="schwab")
            extend_pending(pending, pending_daily(items, states, target_day))
            daily_target = target_day
        week = local_day.isocalendar()[:2]
        minute_target = min(MINUTE_TIER_SIZE, len(items))
        if (
            schwab.token_ready(now)
            and now >= next_rank_attempt
            and (len(minute_items) < minute_target or ranked_week != week)
        ):
            minute_items = select_minute_tier(storage, schwab, items)
            minute_states = storage.states("1min", source="schwab")
            minute_pending = [
                item
                for item in minute_items
                if not minute_states.get(item[2], {}).get("history_complete")
            ]
            ranked_week = week
            next_rank_attempt = now + timedelta(
                days=7 if len(minute_items) == minute_target else 0,
                minutes=0 if len(minute_items) == minute_target else 15,
            )
            safe_source_status(
                storage,
                "schwab",
                "ready" if len(minute_items) == minute_target else "incomplete",
                f"{len(minute_items)}/{minute_target} eligible minute instruments configured",
            )
        elif not schwab.token_ready(now):
            safe_source_status(
                storage, "schwab", "auth_required", "Authenticate Schwab on the broker-auth page"
            )
        session = bounds(local_day)
        live_window = session and session[0] + timedelta(minutes=5) <= now <= session[
            1
        ] + timedelta(minutes=5)
        due_index = next(
            (
                index
                for index, item in enumerate(pending)
                if now >= retry_at.get(item["instrument_id"], datetime.min.replace(tzinfo=UTC))
            ),
            None,
        )
        daily_due = due_index is not None
        if args.daemon and not daily_due and live_window and minute_items and now >= next_live:
            sweep_started = datetime.now(UTC)
            next_live = sweep_started + LIVE_INTERVAL
            ok = failed = 0
            for item in minute_items:
                if stop.is_set():
                    break
                try:
                    collect(storage, schwab, item, "1min", now=datetime.now(UTC), live=True)
                    ok += 1
                except AuthRequired:
                    # Token rolled over mid-sweep; retry the whole tier shortly instead of
                    # failing every remaining symbol.
                    log.warning("Live sweep paused at %s: Schwab token unavailable", item[0])
                    next_live = datetime.now(UTC) + timedelta(seconds=60)
                    failed += 1
                    break
                except COLLECTION_ERRORS as exc:
                    log.error("Live %s failed: %s", item[0], type(exc).__name__)
                    failed += 1
            log.info(
                "Live sweep: %d/%d ok, %d failed in %.0fs",
                ok,
                len(minute_items),
                failed,
                (datetime.now(UTC) - sweep_started).total_seconds(),
            )
        if schwab.token_ready(now) and not daily_due and minute_pending:
            item = minute_pending.pop(0)
            retry_key = item[2]
            if now >= minute_retry_at.get(retry_key, datetime.min.replace(tzinfo=UTC)):
                try:
                    complete = collect(storage, schwab, item, "1min", now=now)
                    if complete:
                        minute_retry_attempts.pop(retry_key, None)
                    else:
                        schedule_retry(minute_retry_at, minute_retry_attempts, retry_key, now)
                        minute_pending.append(item)
                except COLLECTION_ERRORS as exc:
                    log.error("Minute recovery %s failed: %s", item[0], type(exc).__name__)
                    schedule_retry(minute_retry_at, minute_retry_attempts, retry_key, now)
                    minute_pending.append(item)
            else:
                minute_pending.append(item)
        if schwab.token_ready(now) and daily_due:
            item = pending.pop(due_index)
            if now >= retry_at.get(item["instrument_id"], datetime.min.replace(tzinfo=UTC)):
                try:
                    daily_item = (
                        item["symbol"],
                        schwab_symbol(item["symbol"]),
                        item["instrument_id"],
                    )
                    complete = collect(
                        storage,
                        schwab,
                        daily_item,
                        "daily",
                        now=now,
                        universe_name=item.get("universe", FULL_UNIVERSE),
                    )
                    if complete:
                        retry_attempts.pop(item["instrument_id"], None)
                    else:
                        schedule_retry(retry_at, retry_attempts, item["instrument_id"], now)
                        pending.append(item)
                except COLLECTION_ERRORS as exc:
                    log.error("Daily recovery %s failed: %s", item["symbol"], type(exc).__name__)
                    schedule_retry(retry_at, retry_attempts, item["instrument_id"], now)
                    pending.append(item)
            else:
                pending.append(item)
        if (now - last_heartbeat).total_seconds() >= 60:
            if not schwab.token_ready(now):
                safe_source_status(
                    storage,
                    "schwab",
                    "auth_required",
                    "Authenticate Schwab on the broker-auth page",
                )
            else:
                safe_source_status(storage, "schwab", *collection_status(storage, pending, items))
            last_heartbeat = now
        if not args.daemon and not pending and not minute_pending:
            return 0
        stop.wait(1 if pending else 30)
    safe_source_status(storage, "schwab", "stopped", "Collector stopped gracefully")
    return 0


def run(args, storage, client):
    items = []
    retry_at = {}
    next_live = datetime.min.replace(tzinfo=UTC)
    last_heartbeat = datetime.min.replace(tzinfo=UTC)
    health = ("ready", "Schwab collection ready")
    had_failure = False
    while not stop.is_set():
        now = datetime.now(UTC)
        if not client.token_ready(now):
            storage.source_status("auth_required", "Authenticate Schwab on the broker-auth page")
            if not args.daemon:
                return 1
            stop.wait(60)
            continue
        if not items:
            try:
                items = client.configure_pilot(
                    storage, [symbol for symbol, _ in universe(args.universe)], args.universe
                )
            except COLLECTION_ERRORS as exc:
                storage.source_status(
                    "error", f"Pilot reference refresh failed: {type(exc).__name__}"
                )
                if not args.daemon:
                    return 1
                stop.wait(900)
                continue
        session = bounds(now.astimezone(NY).date())
        if (
            args.daemon
            and session
            and session[0] + timedelta(minutes=5) <= now <= session[1] + timedelta(minutes=5)
            and now >= next_live
        ):
            for item in items:
                if stop.is_set():
                    break
                try:
                    collect(storage, client, item, "1min", now=datetime.now(UTC), live=True)
                except COLLECTION_ERRORS as exc:
                    log.error("Live %s failed: %s", item[0], type(exc).__name__)
            next_live = datetime.now(UTC) + timedelta(seconds=300)
        target = session_bounds(latest_completed(now))[1]
        pending = []
        for item in items:
            for resolution in ("1min", "daily"):
                state = storage.state(item[2], resolution)
                if (
                    not state["history_complete"]
                    or not state["checked_through"]
                    or state["checked_through"] < target
                    or state["error"]
                    or not state["full_refreshed_at"]
                    or now - state["full_refreshed_at"] >= timedelta(days=7)
                ) and now >= retry_at.get((item[0], resolution), datetime.min.replace(tzinfo=UTC)):
                    pending.append((item, resolution))
        # One historical request per tick lets live polling preempt long startup backfills.
        for item, resolution in pending[:1] if args.daemon else pending:
            if stop.is_set():
                break
            try:
                complete = collect(storage, client, item, resolution, now=now)
                if not complete:
                    retry_at[(item[0], resolution)] = now + timedelta(minutes=15)
                    had_failure = True
                health = ("ready", "Schwab collection ready")
            except COLLECTION_ERRORS as exc:
                had_failure = True
                health = (
                    "auth_required" if isinstance(exc, AuthRequired) else "error",
                    f"{item[0]} {resolution}: {type(exc).__name__}; retry scheduled",
                )
                log.error("Recovery %s %s failed: %s", item[0], resolution, type(exc).__name__)
                retry_at[(item[0], resolution)] = now + timedelta(minutes=15)
        if (now - last_heartbeat).total_seconds() >= 60 or not args.daemon:
            unresolved = storage.unresolved_series()
            if unresolved and health[0] == "ready":
                health = (
                    "incomplete",
                    f"{unresolved} series have unresolved collection errors or gaps",
                )
            elif not unresolved and health[0] == "incomplete":
                health = ("ready", "Schwab collection ready")
            storage.source_status(*health)
            last_heartbeat = now
        if not args.daemon:
            return int(had_failure)
        stop.wait(1 if pending else 30)
    storage.source_status("stopped", "Collector stopped gracefully")
    return 0


def main(argv: list[str] | None = None):
    import fcntl  # Production runs on Linux; import here keeps testable functions portable.

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default=FULL_UNIVERSE)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument(
        "--backfill", action="store_true", help="Explicit one-shot history/recovery (default)"
    )
    args = parser.parse_args(argv)
    configure_logging(component="ingest-us", service="ingest-us")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    lock_path = CollectionSettings().ingest_lock
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Another US collector holds the shared lock")
            return 2
        storage = USStorage.from_environment()
        client = EngineClient()
        try:
            storage.seed_us_reference_data()
            if args.universe == FULL_UNIVERSE:
                return run_full(args, storage, client)
            return run(args, storage, client)
        finally:
            client.close()
            storage.client.close()


if __name__ == "__main__":
    sys.exit(main())
