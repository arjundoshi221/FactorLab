"""Poll Upstox intraday candles and write raw plus curated data to ClickHouse."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.countries.in_.equities.upstox.auth import ensure_token
from factorlab.countries.in_.equities.upstox.candles import (
    MARKET_QUOTE_BATCH_SIZE,
    UpstoxFetchError,
    UpstoxRateLimiter,
    fetch_historical_candles,
    fetch_intraday_candles,
    fetch_market_quote_ohlc,
    historical_windows,
    instrument_key_batches,
)
from factorlab.countries.in_.equities.upstox.client import get_session
from factorlab.countries.in_.equities.upstox.instruments import (
    find_equities,
    find_nearest_future,
    load_or_download,
)
from factorlab.countries.in_.equities.upstox.universes import load_universe
from factorlab.storage.clickhouse import ClickHouseStorage

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")
CALENDAR_KEY = "XBOM"
INSTRUMENTS_CACHE_DIR = PROJECT_ROOT / "data" / "upstox" / "instruments"
POLL_INTERVAL_SECONDS = 300
FUT_POLL_INTERVAL_SECONDS = 600
RECOVERY_RETRY_SECONDS = 900
TOKEN_RETRY_SECONDS = 60
FULL_EQUITY_UNIVERSE = "full_nse_eq"
MARKET_OPEN_UTC = datetime_time(3, 45)
# Two-minute grace permits a 10:01 UTC sweep after Upstox has finalized the
# 09:59 UTC (15:29 IST) candle.
MARKET_CLOSE_UTC = datetime_time(10, 2)

log = logging.getLogger("factorlab.upstox.clickhouse")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_shutdown = False


def _handle_shutdown(signum, frame) -> None:
    del frame
    global _shutdown
    log.info("Received signal %s; stopping after the current batch", signum)
    _shutdown = True


signal.signal(signal.SIGINT, _handle_shutdown)
signal.signal(signal.SIGTERM, _handle_shutdown)


@dataclass(frozen=True)
class CandleSeries:
    """One Upstox series mapped to its canonical ClickHouse identity."""

    instrument_key: str
    instrument_id: uuid.UUID
    symbol: str
    contract_id: uuid.UUID | None = None

    def storage_key(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "contract_id": self.contract_id,
            "symbol": self.symbol,
        }


def build_series(
    symbols,
    equity_lookup,
    instruments,
    instrument_lookup,
    contract_lookup,
) -> list[CandleSeries]:
    """Resolve the configured universe to equity and nearest-future series."""

    series = []
    for symbol in symbols:
        equity = equity_lookup.get(symbol)
        instrument_id = instrument_lookup.get(symbol)
        if equity and instrument_id:
            series.append(CandleSeries(equity["instrument_key"], instrument_id, symbol))
        future = find_nearest_future(instruments, symbol)
        if future and instrument_id and future["instrument_key"] in contract_lookup:
            series.append(CandleSeries(
                future["instrument_key"],
                instrument_id,
                symbol,
                contract_lookup[future["instrument_key"]],
            ))
    return series


def build_full_equity_series(
    instruments,
    instrument_lookup,
) -> list[CandleSeries]:
    """Resolve every active NSE cash equity in the daily Upstox master."""

    series = []
    seen_keys: set[str] = set()
    equities = sorted(
        (
            record
            for record in instruments
            if record.get("segment") == "NSE_EQ"
            and record.get("instrument_type") == "EQ"
        ),
        key=lambda record: (
            str(record.get("trading_symbol") or ""),
            str(record.get("instrument_key") or ""),
        ),
    )
    for equity in equities:
        instrument_key = str(equity.get("instrument_key") or "")
        symbol = str(equity.get("trading_symbol") or "")
        instrument_id = instrument_lookup.get(symbol)
        if not instrument_key or not symbol or instrument_id is None or instrument_key in seen_keys:
            continue
        seen_keys.add(instrument_key)
        series.append(CandleSeries(instrument_key, instrument_id, symbol))
    return series


def configure_collection_universe(
    storage: ClickHouseStorage,
    universe: str,
) -> tuple[list[CandleSeries], bool, int]:
    """Refresh the daily master and activate exactly the requested series."""

    instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
    instrument_lookup = storage.sync_instruments(instruments)
    full_equity_mode = universe == FULL_EQUITY_UNIVERSE
    if full_equity_mode:
        series = build_full_equity_series(instruments, instrument_lookup)
        if not series:
            raise RuntimeError("The Upstox master contained no NSE cash equities")
    else:
        contract_lookup = storage.sync_contracts(instruments, instrument_lookup)
        equity_lookup = find_equities(instruments)
        symbols = load_universe(universe, PROJECT_ROOT)
        series = build_series(
            symbols,
            equity_lookup,
            instruments,
            instrument_lookup,
            contract_lookup,
        )
    expected_count = storage.sync_expected_india_series(
        [item.storage_key() for item in series],
        source="upstox",
        universe=universe,
    )
    log.info(
        "Activated %d expected series for universe %s (%s)",
        expected_count,
        universe,
        "batched OHLC quotes" if full_equity_mode else "per-series candles",
    )
    return series, full_equity_mode, expected_count


def poll_once(
    session,
    series,
    storage,
    limiter,
    *,
    include_futures: bool,
) -> tuple[int, int, int]:
    successful = 0
    failed = 0
    rows_written = 0
    for item in series:
        if _shutdown:
            break
        if item.contract_id is not None and not include_futures:
            continue

        try:
            frame, raw_id = fetch_intraday_candles(
                session, item.instrument_key, storage, limiter=limiter
            )
            if not frame.empty:
                rows_written += storage.write_candles_1min(
                    frame,
                    instrument_id=item.instrument_id,
                    contract_id=item.contract_id,
                    symbol=item.symbol,
                    raw_id=raw_id,
                )
                successful += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            log.exception("Failed to collect intraday candles for %s", item.instrument_key)
    return successful, failed, rows_written


def poll_full_equity_once(
    session,
    series: list[CandleSeries],
    storage,
    limiter,
    *,
    batch_size: int = MARKET_QUOTE_BATCH_SIZE,
) -> tuple[int, int, int]:
    """Collect finalized one-minute candles for the full NSE equity master."""

    by_key = {item.instrument_key: item for item in series}
    successful = 0
    failed = 0
    rows_written = 0
    key_batches = instrument_key_batches(list(by_key), batch_size=batch_size)

    for index, keys in enumerate(key_batches, start=1):
        if _shutdown:
            failed += sum(len(batch) for batch in key_batches[index - 1:])
            break
        try:
            result = fetch_market_quote_ohlc(
                session,
                keys,
                storage,
                limiter=limiter,
            )
            observed = result.observed_keys.intersection(keys)
            successful += len(observed)
            failed += len(keys) - len(observed)
            candle_batches = []
            for instrument_key, frame in result.candles.items():
                item = by_key.get(instrument_key)
                if item is None or frame.empty:
                    continue
                candle_batches.append({
                    "candles": frame,
                    "instrument_id": item.instrument_id,
                    "contract_id": None,
                    "symbol": item.symbol,
                    "raw_id": result.raw_id,
                })
            if candle_batches:
                rows_written += storage.write_candles_1min_batch(candle_batches)
            if len(observed) != len(keys):
                log.warning(
                    "OHLC batch %d/%d returned %d of %d requested instruments",
                    index,
                    len(key_batches),
                    len(observed),
                    len(keys),
                )
        except Exception:
            failed += len(keys)
            log.exception(
                "Failed full-universe OHLC batch %d/%d (%d instruments)",
                index,
                len(key_batches),
                len(keys),
            )
    return successful, failed, rows_written


def seconds_until_next_quote_sweep(
    *,
    now_epoch: float | None = None,
    sample_delay_seconds: float = 5.0,
) -> float:
    """Align quote sweeps just after each wall-clock minute boundary."""

    timestamp = time.time() if now_epoch is None else now_epoch
    return 60.0 - (timestamp % 60.0) + sample_delay_seconds


def previous_completed_session(calendar, now: datetime) -> tuple[date, datetime]:
    """Return the most recent exchange session before the current India date."""

    local_today = now.astimezone(IST).date()
    search_start = local_today - timedelta(days=30)
    sessions = calendar.sessions_in_range(
        search_start.isoformat(),
        (local_today - timedelta(days=1)).isoformat(),
    )
    if len(sessions) == 0:
        raise RuntimeError("No completed NSE session found in the previous 30 days")
    session = sessions[-1]
    close = calendar.session_close(session).to_pydatetime().astimezone(UTC)
    return session.date(), close


def recover_historical(
    session,
    series: list[CandleSeries],
    storage: ClickHouseStorage,
    calendar,
    limiter: UpstoxRateLimiter,
    *,
    universe: str,
    now: datetime,
) -> bool:
    """Recover every series through the most recently completed NSE session."""

    end_date, session_close = previous_completed_session(calendar, now)
    latest = storage.latest_candle_times(
        [item.storage_key() for item in series],
        before=session_close,
    )
    no_contract = uuid.UUID(int=0)
    expected_last_bar = session_close - timedelta(minutes=1)
    plans: list[tuple[CandleSeries, list[tuple[date, date]]]] = []
    for item in series:
        key = (item.instrument_id, item.contract_id or no_contract)
        last_bar = latest.get(key)
        if last_bar is None:
            start_date = end_date
        else:
            last_timestamp = pd.Timestamp(last_bar)
            if last_timestamp.tzinfo is None:
                last_timestamp = last_timestamp.tz_localize("UTC")
            else:
                last_timestamp = last_timestamp.tz_convert("UTC")
            if last_timestamp >= pd.Timestamp(expected_last_bar):
                continue
            start_date = last_timestamp.tz_convert(IST).date()
        windows = historical_windows(start_date, end_date)
        if windows:
            plans.append((item, windows))

    if not plans:
        log.info("Historical recovery is current through %s", end_date)
        return True

    run = storage.start_ingestion_run(
        pipeline="india_historical_1min_recovery",
        source="upstox",
        universe=universe,
        requested_series=len(plans),
        metadata={
            "from_date": min(windows[0][0] for _, windows in plans).isoformat(),
            "to_date": end_date.isoformat(),
            "trigger": "automatic",
            "chunk_days": 30,
        },
    )
    successful = 0
    failed = 0
    rows_written = 0
    errors: list[str] = []
    consecutive_systemic_failures = 0

    for index, (item, windows) in enumerate(plans):
        if _shutdown:
            failed += len(plans) - index
            errors.append("shutdown requested")
            break
        try:
            for window_start, window_end in windows:
                frame, raw_id = fetch_historical_candles(
                    session,
                    item.instrument_key,
                    window_start,
                    window_end,
                    storage,
                    limiter=limiter,
                )
                if not frame.empty:
                    frame = frame[frame["timestamp"] < pd.Timestamp(session_close)]
                    rows_written += storage.write_candles_1min(
                        frame,
                        instrument_id=item.instrument_id,
                        contract_id=item.contract_id,
                        symbol=item.symbol,
                        raw_id=raw_id,
                    )
            successful += 1
            consecutive_systemic_failures = 0
        except UpstoxFetchError as exc:
            failed += 1
            errors.append(f"{item.instrument_key}: {exc}")
            consecutive_systemic_failures = (
                consecutive_systemic_failures + 1 if exc.retriable else 0
            )
            log.warning("Historical recovery failed for %s: %s", item.instrument_key, exc)
            if consecutive_systemic_failures >= 3:
                remaining = len(plans) - index - 1
                failed += remaining
                errors.append(f"aborted after systemic failures; {remaining} series deferred")
                break
        except Exception as exc:
            failed += 1
            consecutive_systemic_failures = 0
            errors.append(f"{item.instrument_key}: {exc}")
            log.exception("Historical recovery failed for %s", item.instrument_key)

    status = "success" if failed == 0 else "partial"
    storage.finish_ingestion_run(
        run,
        status=status,
        successful_series=successful,
        failed_series=failed,
        rows_written=rows_written,
        error="; ".join(errors)[:2000] or None,
    )
    log.info(
        "Historical recovery through %s completed: %d series, %d failed, %d rows",
        end_date,
        successful,
        failed,
        rows_written,
    )
    return failed == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Upstox ClickHouse 1-minute poller")
    parser.add_argument("--universe", default="demo")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run one polling batch regardless of market hours")
    parser.add_argument(
        "--quote-batch-size",
        type=int,
        default=MARKET_QUOTE_BATCH_SIZE,
        help="Instrument keys per full-universe OHLC quote request (default: 100)",
    )
    parser.add_argument(
        "--skip-recovery",
        action="store_true",
        help="Skip automatic historical recovery for emergency operations",
    )
    args = parser.parse_args()
    if args.quote_batch_size < 1:
        parser.error("--quote-batch-size must be positive")

    storage = ClickHouseStorage.from_environment()
    storage.seed_india_reference_data()

    series, full_equity_mode, expected_count = configure_collection_universe(
        storage,
        args.universe,
    )
    configured_for = datetime.now(IST).date()

    while not _shutdown:
        try:
            token = ensure_token(interactive=False)
            session = get_session(token)
            break
        except Exception:
            if not args.daemon:
                raise
            log.exception(
                "No valid Upstox token; expected-series configuration is active. "
                "Retrying authentication in %d seconds",
                TOKEN_RETRY_SECONDS,
            )
            time.sleep(TOKEN_RETRY_SECONDS)
    else:
        return 0

    last_future_poll = 0.0
    calendar = xcals.get_calendar(CALENDAR_KEY)
    limiter = UpstoxRateLimiter()
    recovery_completed_for: date | None = None
    next_recovery_attempt = 0.0

    while not _shutdown:
        now = datetime.now(UTC)
        recovery_date = now.astimezone(IST).date()
        if recovery_date != configured_for:
            try:
                series, full_equity_mode, expected_count = configure_collection_universe(
                    storage,
                    args.universe,
                )
                configured_for = recovery_date
            except Exception:
                log.exception(
                    "Daily Upstox instrument-master refresh failed; retaining the prior universe"
                )
        if (
            not args.skip_recovery
            and not full_equity_mode
            and recovery_completed_for != recovery_date
            and time.monotonic() >= next_recovery_attempt
        ):
            try:
                recovered = recover_historical(
                    session,
                    series,
                    storage,
                    calendar,
                    limiter,
                    universe=args.universe,
                    now=now,
                )
            except Exception:
                recovered = False
                log.exception("Automatic historical recovery failed before planning requests")
            if recovered:
                recovery_completed_for = recovery_date
            else:
                next_recovery_attempt = time.monotonic() + RECOVERY_RETRY_SECONDS

        today = now.strftime("%Y-%m-%d")
        if not args.once and len(calendar.sessions_in_range(today, today)) == 0:
            if not args.daemon:
                return 10
            time.sleep(3600)
            continue

        if not args.once and now.time() < MARKET_OPEN_UTC:
            open_at = datetime.combine(now.date(), MARKET_OPEN_UTC, tzinfo=UTC)
            time.sleep(max((open_at - now).total_seconds(), 1))
            continue
        if not args.once and now.time() > MARKET_CLOSE_UTC:
            if not args.daemon:
                return 0
            time.sleep(max((datetime.combine(now.date() + timedelta(days=1), MARKET_OPEN_UTC, tzinfo=UTC) - now).total_seconds(), 1))
            continue

        include_futures = (
            not full_equity_mode
            and time.monotonic() - last_future_poll >= FUT_POLL_INTERVAL_SECONDS
        )
        run = storage.start_ingestion_run(
            pipeline="india_intraday_1min",
            source="upstox",
            universe=args.universe,
            requested_series=(
                expected_count
                if include_futures or full_equity_mode
                else sum(item.contract_id is None for item in series)
            ),
            metadata={
                "include_futures": include_futures,
                "collection_method": (
                    "market_quote_ohlc_v3_batch"
                    if full_equity_mode
                    else "intraday_candle_v3_per_series"
                ),
                "quote_batch_size": args.quote_batch_size if full_equity_mode else None,
            },
        )
        try:
            if full_equity_mode:
                successful, failed, rows_written = poll_full_equity_once(
                    session,
                    series,
                    storage,
                    limiter,
                    batch_size=args.quote_batch_size,
                )
            else:
                successful, failed, rows_written = poll_once(
                    session,
                    series,
                    storage,
                    limiter,
                    include_futures=include_futures,
                )
            requested = (
                expected_count
                if include_futures or full_equity_mode
                else sum(item.contract_id is None for item in series)
            )
            failed = max(failed, requested - successful)
            run_status = "success" if failed == 0 else "partial"
            storage.finish_ingestion_run(
                run,
                status=run_status,
                successful_series=successful,
                failed_series=failed,
                rows_written=rows_written,
            )
        except Exception as exc:
            storage.finish_ingestion_run(run, status="failed", error=str(exc)[:2000])
            raise
        log.info(
            "Collected %d series (%d failed) and wrote %d candle rows",
            successful,
            failed,
            rows_written,
        )
        if include_futures:
            last_future_poll = time.monotonic()
        if args.once:
            return 0
        if full_equity_mode:
            time.sleep(seconds_until_next_quote_sweep())
        else:
            time.sleep(POLL_INTERVAL_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
