"""Production US price collection. One process owns live polling and resumable backfill."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml

from factorlab.countries.us.equities.eodhd.us_universe import schwab_symbol
from factorlab.sources.schwab.market import (
    NY,
    AuthRequired,
    MarketClient,
    bounds,
    latest_completed,
    token_ready,
)
from factorlab.storage.v2_us import V2USStorage as USStorage

log = logging.getLogger("factorlab.schwab")
stop = threading.Event()
FULL_UNIVERSE = "us_listed_equities"
MINUTE_UNIVERSE = "us_liquid_250"
MINUTE_TIER_SIZE = 250


def universe(name):
    config = yaml.safe_load((ROOT / "configs/universes/us.yaml").read_text())
    symbols = config["universes"][name]["symbols"]
    return [(s.removesuffix(".US"), s.removesuffix(".US").replace("-", "/")) for s in symbols]


def collect(storage, client, item, resolution, *, now, live=False, universe_name="pilot"):
    symbol, provider, instrument_id = item
    state = storage.state(instrument_id, resolution)
    state["symbol"] = symbol
    full = not state["history_complete"] or not state["full_refreshed_at"] or (
        now - state["full_refreshed_at"] >= timedelta(days=7))
    end = now if live else bounds(latest_completed(now))[1]
    if live:
        opened = bounds(now.astimezone(NY).date())[0]
        start = max(opened, state["last_bar"] - timedelta(minutes=2)) if state.get("last_bar") else opened
    elif full:
        start = datetime(1970, 1, 1, tzinfo=UTC) if resolution == "daily" else now - timedelta(days=60)
    else:
        start = state["checked_through"] - timedelta(days=7)
        if state.get("error") and not str(state["error"]).startswith(
                "Invalid Schwab candles:"):
            gap = storage.gap_start(instrument_id, resolution, now)
            if gap:
                start = min(start, gap)
        if resolution == "1min":
            start = max(start, now - timedelta(days=60))
    handle = storage.start_ingestion_run(pipeline="us_live" if live else f"us_recovery_{resolution}",
        market_code="USA", source="schwab", universe=universe_name, requested_series=1,
        metadata={"symbol": symbol, "resolution": resolution, "from": start.isoformat(), "to": end.isoformat(), "full": full and not live})
    try:
        frame, raw_id = client.candles(provider, resolution, start, end)
        if frame.empty:
            raise ValueError("No regular-session candles returned; coverage remains unresolved")
        invalid_candles = int(frame.attrs.get("invalid_candles", 0))
        prior_error = state.get("error")
        count = (storage.write_daily(frame, instrument_id=instrument_id, symbol=symbol, raw_id=raw_id)
                 if resolution == "daily" else storage.write_candles_1min(frame,
                    instrument_id=instrument_id, symbol=symbol, raw_id=raw_id, source="schwab", market_code="USA"))
        first = frame["timestamp"].min().to_pydatetime()
        last = frame["timestamp"].max().to_pydatetime()
        state["available_from"] = min(state["available_from"] or first, first)
        state["last_bar"] = max(state["last_bar"] or last, last)
        missing = storage.coverage(instrument_id, symbol, resolution, start, end, state["available_from"]) or 0
        if not live:
            state.update(history_complete=True, checked_through=end)
            if full:
                state["full_refreshed_at"] = now
        invalid_detail = None
        if invalid_candles:
            invalid_detail = f"Invalid Schwab candles: {invalid_candles} skipped"
        elif not full and isinstance(prior_error, str) and prior_error.startswith(
                "Invalid Schwab candles:"):
            invalid_detail = prior_error
        coverage_detail = f"Coverage incomplete: {missing} missing bars" if missing else None
        state["error"] = "; ".join(filter(None, (invalid_detail, coverage_detail))) or None
        storage.save_state(state)
        partial = bool(state["error"])
        storage.finish_ingestion_run(handle, status="partial" if partial else "success",
            successful_series=0 if partial else 1, failed_series=1 if partial else 0, rows_written=count,
            error=state["error"])
        log.info("%s %s: %d rows through %s", symbol, resolution, count, last.isoformat())
        return not missing or bool(invalid_detail)
    except Exception as exc:
        # Exception messages here are our own sanitized descriptions, never HTTP response bodies.
        detail = str(exc) if isinstance(exc, (ValueError, AuthRequired, RuntimeError)) else type(exc).__name__
        state["error"] = detail[:300]
        storage.save_state(state)
        storage.finish_ingestion_run(handle, status="failed", failed_series=1, error=detail[:300])
        raise


def safe_source_status(storage, source, status, detail):
    try:
        storage.source_status(status, detail, source=source)
    except TypeError:
        # Compatibility with older storage doubles and images.
        storage.source_status(status, detail)
    except Exception as exc:  # noqa: BLE001 - source health must survive status-write failures
        log.error("Source status write failed for %s: %s", source, type(exc).__name__)


def pending_daily(items, states, target_day):
    """Return instruments needing history or a per-symbol daily fallback."""
    target = bounds(target_day)[1]
    pending = []
    for item in items:
        state = states.get(item["instrument_id"], {})
        error = state.get("error")
        if (not state.get("history_complete") or not state.get("checked_through")
                or state["checked_through"] < target
                or (error and not str(error).startswith("Invalid Schwab candles:"))):
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
    for candidate in storage.liquid_candidates(limit=target + 150):
        item = by_id.get(candidate["instrument_id"])
        if item is None:
            continue
        provider = schwab_symbol(item["symbol"])
        selected.append({**item, "provider_symbol": provider})
        if len(selected) == target:
            break
    storage.sync_expected_series(
        selected, source="schwab", universe=MINUTE_UNIVERSE, resolution="1min")
    return [(item["symbol"], item["provider_symbol"], item["instrument_id"]) for item in selected]


def run_full(args, storage):
    schwab = MarketClient(storage)
    safe_source_status(
        storage, "schwab", "ready" if token_ready() else "auth_required",
        "Schwab token available" if token_ready() else
        "Authenticate Schwab on the broker-auth page")
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
                minute_retry_at = {key: value for key, value in minute_retry_at.items()
                                   if key in new_ids}
                minute_items = [item for item in minute_items if item[2] in new_ids]
                minute_pending = [item for item in minute_pending if item[2] in new_ids]
                if removed:
                    retained = [item for item in storage.active_expected_series(
                        source="schwab", resolution="1min")
                        if item["instrument_id"] in new_ids]
                    storage.sync_expected_series(
                        retained, source="schwab", universe=MINUTE_UNIVERSE,
                        resolution="1min")
                    minute_items = [(item["symbol"], item["provider_symbol"],
                                     item["instrument_id"]) for item in retained]
                # Force daily recovery and minute ranking to account for changes now.
                daily_target = None
                ranked_week = None
                next_rank_attempt = datetime.min.replace(tzinfo=UTC)
                log.info("Loaded configured universe: %d stocks (%d added, %d removed)",
                         len(items), added, removed)
            next_membership_poll = now + timedelta(seconds=60)
        if not items:
            safe_source_status(storage, "schwab", "waiting",
                               "Waiting for universe-us to publish a configured universe")
            if not args.daemon:
                return 1
            stop.wait(30)
            continue
        if not token_ready(now) and not args.daemon:
            safe_source_status(storage, "schwab", "auth_required",
                               "Authenticate Schwab on the broker-auth page")
            return 1
        target_day = latest_completed(now)
        if daily_target != target_day:
            states = storage.states("daily", source="schwab")
            extend_pending(pending, pending_daily(items, states, target_day))
            daily_target = target_day
        week = local_day.isocalendar()[:2]
        minute_target = min(MINUTE_TIER_SIZE, len(items))
        if (token_ready(now) and now >= next_rank_attempt
                and (len(minute_items) < minute_target or ranked_week != week)):
            minute_items = select_minute_tier(storage, schwab, items)
            minute_states = storage.states("1min", source="schwab")
            minute_pending = [item for item in minute_items
                              if not minute_states.get(item[2], {}).get("history_complete")]
            ranked_week = week
            next_rank_attempt = now + timedelta(
                days=7 if len(minute_items) == minute_target else 0,
                minutes=0 if len(minute_items) == minute_target else 15,
            )
            safe_source_status(
                storage, "schwab", "ready" if len(minute_items) == minute_target else "incomplete",
                f"{len(minute_items)}/{minute_target} eligible minute instruments configured")
        elif not token_ready(now):
            safe_source_status(storage, "schwab", "auth_required",
                               "Authenticate Schwab on the broker-auth page")
        session = bounds(local_day)
        live_window = session and session[0] + timedelta(minutes=5) <= now <= session[1] + timedelta(minutes=5)
        if args.daemon and live_window and minute_items and now >= next_live:
            for item in minute_items:
                if stop.is_set():
                    break
                try:
                    collect(storage, schwab, item, "1min", now=datetime.now(UTC), live=True)
                except Exception as exc:  # noqa: BLE001 - continue other live symbols
                    log.error("Live %s failed: %s", item[0], type(exc).__name__)
            next_live = datetime.now(UTC) + timedelta(seconds=300)
        if token_ready(now) and minute_pending:
            item = minute_pending.pop(0)
            retry_key = item[2]
            if now >= minute_retry_at.get(retry_key, datetime.min.replace(tzinfo=UTC)):
                try:
                    complete = collect(storage, schwab, item, "1min", now=now)
                    if not complete:
                        minute_retry_at[retry_key] = now + timedelta(minutes=15)
                        minute_pending.append(item)
                except Exception as exc:  # noqa: BLE001 - retry one failed recovery later
                    log.error("Minute recovery %s failed: %s", item[0], type(exc).__name__)
                    minute_retry_at[retry_key] = now + timedelta(minutes=15)
                    minute_pending.append(item)
            else:
                minute_pending.append(item)
        if token_ready(now) and pending:
            item = pending.pop(0)
            if now >= retry_at.get(item["instrument_id"], datetime.min.replace(tzinfo=UTC)):
                try:
                    daily_item = (item["symbol"], schwab_symbol(item["symbol"]),
                                  item["instrument_id"])
                    collect(storage, schwab, daily_item, "daily", now=now,
                            universe_name=item.get("universe", FULL_UNIVERSE))
                except Exception as exc:  # noqa: BLE001 - retry one failed daily recovery later
                    log.error("Daily recovery %s failed: %s", item["symbol"], type(exc).__name__)
                    retry_at[item["instrument_id"]] = now + timedelta(minutes=15)
                    pending.append(item)
            else:
                pending.append(item)
        if (now - last_heartbeat).total_seconds() >= 60:
            if not token_ready(now):
                safe_source_status(storage, "schwab", "auth_required",
                                   "Authenticate Schwab on the broker-auth page")
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
        if not token_ready(now):
            storage.source_status("auth_required", "Authenticate Schwab on the broker-auth page")
            if not args.daemon:
                return 1
            stop.wait(60)
            continue
        if not items:
            for symbol, provider in universe(args.universe):
                if stop.is_set():
                    break
                try:
                    record, raw_id = client.instrument(provider)
                    identifier = storage.reference(symbol, provider, record, raw_id, args.universe)
                    items.append((symbol, provider, identifier))
                except Exception as exc:  # noqa: BLE001 - report unresolved provider rows
                    log.error("Reference %s failed: %s", symbol, type(exc).__name__)
                    health = ("error", f"Reference lookup failed for {symbol}")
                    had_failure = True
            if len(items) != len(universe(args.universe)):
                storage.source_status(*health)
                items = []
                if not args.daemon:
                    return 1
                stop.wait(900)
                continue
        session = bounds(now.astimezone(NY).date())
        if args.daemon and session and session[0] + timedelta(minutes=5) <= now <= session[1] + timedelta(minutes=5) and now >= next_live:
            for item in items:
                if stop.is_set():
                    break
                try:
                    collect(storage, client, item, "1min", now=datetime.now(UTC), live=True)
                except Exception as exc:  # noqa: BLE001 - continue other live symbols
                    log.error("Live %s failed: %s", item[0], type(exc).__name__)
            next_live = datetime.now(UTC) + timedelta(seconds=300)
        target = bounds(latest_completed(now))[1]
        pending = []
        for item in items:
            for resolution in ("1min", "daily"):
                state = storage.state(item[2], resolution)
                if ((not state["history_complete"] or not state["checked_through"] or state["checked_through"] < target
                     or state["error"] or not state["full_refreshed_at"] or now - state["full_refreshed_at"] >= timedelta(days=7))
                        and now >= retry_at.get((item[0], resolution), datetime.min.replace(tzinfo=UTC))):
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
            except Exception as exc:  # noqa: BLE001 - preserve the daemon and report failure
                had_failure = True
                health = ("auth_required" if isinstance(exc, AuthRequired) else "error",
                          f"{item[0]} {resolution}: {type(exc).__name__}; retry scheduled")
                log.error("Recovery %s %s failed: %s", item[0], resolution, type(exc).__name__)
                retry_at[(item[0], resolution)] = now + timedelta(minutes=15)
        if (now - last_heartbeat).total_seconds() >= 60 or not args.daemon:
            unresolved = storage.unresolved_series()
            if unresolved and health[0] == "ready":
                health = ("incomplete", f"{unresolved} series have unresolved collection errors or gaps")
            elif not unresolved and health[0] == "incomplete":
                health = ("ready", "Schwab collection ready")
            storage.source_status(*health)
            last_heartbeat = now
        if not args.daemon:
            return int(had_failure)
        stop.wait(1 if pending else 30)
    storage.source_status("stopped", "Collector stopped gracefully")
    return 0


def main():
    import fcntl  # Production runs on Linux; import here keeps testable functions portable.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default=FULL_UNIVERSE)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="Explicit one-shot history/recovery (default)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    lock_path = Path(os.getenv("US_INGEST_LOCK", "/app/data/us-ingest.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Another US collector holds the shared lock")
            return 2
        storage = USStorage.from_environment()
        if args.universe == FULL_UNIVERSE:
            return run_full(args, storage)
        return run(args, storage, MarketClient(storage))


if __name__ == "__main__":
    sys.exit(main())
