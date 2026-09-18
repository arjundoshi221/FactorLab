"""Upstox intraday and historical one-minute candle retrieval."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlencode

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.upstox.com"
HISTORY_AVAILABLE_FROM = date(2022, 1, 1)
HISTORICAL_CHUNK_DAYS = 30
MARKET_QUOTE_OHLC_URL = f"{BASE_URL}/v3/market-quote/ohlc"
MARKET_QUOTE_BATCH_SIZE = 100


@dataclass(frozen=True)
class MarketQuoteOHLCBatch:
    """Normalized finalized candles returned by one batched quote request."""

    candles: dict[str, pd.DataFrame]
    observed_keys: frozenset[str]
    raw_id: Any


class UpstoxFetchError(RuntimeError):
    """A candle request failed after applying the configured retry policy."""

    def __init__(self, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.retriable = retriable


class UpstoxRateLimiter:
    """Sliding-window limiter for Upstox's documented standard API buckets."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._windows: tuple[tuple[float, int], ...] = (
            (1.0, 50),
            (60.0, 500),
            (1800.0, 2000),
        )
        self._requests: deque[float] = deque()

    def acquire(self) -> None:
        """Wait until one request fits every configured sliding window."""

        while True:
            now = self._clock()
            longest_window = self._windows[-1][0]
            while self._requests and now - self._requests[0] >= longest_window:
                self._requests.popleft()

            wait_for = 0.0
            timestamps = list(self._requests)
            for window, limit in self._windows:
                recent = [stamp for stamp in timestamps if now - stamp < window]
                if len(recent) >= limit:
                    wait_for = max(wait_for, window - (now - recent[-limit]))
            if wait_for <= 0:
                self._requests.append(now)
                return
            self._sleep(wait_for)


def instrument_key_batches(
    instrument_keys: list[str],
    *,
    batch_size: int = MARKET_QUOTE_BATCH_SIZE,
) -> list[list[str]]:
    """Split instrument keys into stable batches small enough for quote URLs."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    return [
        instrument_keys[index:index + batch_size]
        for index in range(0, len(instrument_keys), batch_size)
    ]


def fetch_market_quote_ohlc(
    session: requests.Session,
    instrument_keys: list[str],
    storage: Any,
    *,
    limiter: UpstoxRateLimiter | None = None,
    timeout: float = 20,
    max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> MarketQuoteOHLCBatch:
    """Fetch the previous, finalized one-minute candle for many instruments.

    Upstox's V3 OHLC quote endpoint accepts comma-separated instrument keys and
    returns ``prev_ohlc`` for the last completed minute. The exact response is
    archived once for the entire batch and linked to every normalized candle.
    """

    if not instrument_keys:
        raise ValueError("instrument_keys must not be empty")

    query = urlencode({"instrument_key": ",".join(instrument_keys), "interval": "I1"})
    url = f"{MARKET_QUOTE_OHLC_URL}?{query}"
    fetch_key = f"{instrument_keys[0]}..{instrument_keys[-1]}"
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        if limiter is not None:
            limiter.acquire()
        try:
            response = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < max_attempts:
                sleep(2 ** (attempt - 1))
                continue
            raise UpstoxFetchError(
                f"Upstox quote request failed for {len(instrument_keys)} instruments: {exc}",
                retriable=True,
            ) from exc

        raw_id = storage.archive_http_response(
            source="upstox_market_quote_ohlc_v3",
            source_url=url,
            response_body=response.content,
            status_code=response.status_code,
            response_headers=dict(response.headers),
            fetch_key=fetch_key,
            content_type=response.headers.get("Content-Type", "application/json"),
            metadata={
                "interval": "I1",
                "instrument_count": len(instrument_keys),
                "first_instrument_key": instrument_keys[0],
                "last_instrument_key": instrument_keys[-1],
            },
        )
        if response.status_code == 200:
            try:
                payload = response.json()
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    raise TypeError("data is not an object")
            except (TypeError, ValueError, AttributeError) as exc:
                raise UpstoxFetchError("Upstox returned invalid OHLC quote JSON") from exc
            return _normalize_market_quote_ohlc(data, raw_id)

        retriable = response.status_code == 429 or response.status_code >= 500
        last_error = UpstoxFetchError(
            f"Upstox returned HTTP {response.status_code} for an OHLC batch of "
            f"{len(instrument_keys)} instruments",
            retriable=retriable,
        )
        if not retriable or attempt == max_attempts:
            raise last_error
        sleep(_retry_delay(response, attempt))

    raise UpstoxFetchError(
        f"Upstox quote request failed for {len(instrument_keys)} instruments: {last_error}",
        retriable=True,
    )


def _normalize_market_quote_ohlc(
    data: dict[str, Any],
    raw_id: Any,
    *,
    now: pd.Timestamp | None = None,
) -> MarketQuoteOHLCBatch:
    columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    candles: dict[str, pd.DataFrame] = {}
    observed: set[str] = set()
    current_minute = (
        now if now is not None else pd.Timestamp.now(tz="UTC")
    ).floor("min")

    for response_key, quote_data in data.items():
        if not isinstance(quote_data, dict):
            continue
        instrument_key = str(quote_data.get("instrument_token") or "")
        if not instrument_key:
            # Response keys use ``SEGMENT:SYMBOL`` and cannot be converted back
            # to canonical instrument keys without risking a wrong mapping.
            log.warning("OHLC quote entry %s has no instrument_token", response_key)
            continue
        observed.add(instrument_key)
        rows = []
        for candle_name in ("prev_ohlc", "live_ohlc"):
            candle = quote_data.get(candle_name)
            if not isinstance(candle, dict) or candle.get("ts") is None:
                continue
            try:
                candle_time = pd.to_datetime(candle["ts"], unit="ms", utc=True)
                if candle_name == "live_ohlc" and candle_time >= current_minute:
                    continue
                open_price = float(candle["open"])
                high_price = float(candle["high"])
                low_price = float(candle["low"])
                close_price = float(candle["close"])
                if (
                    not all(math.isfinite(value) for value in (
                        open_price, high_price, low_price, close_price,
                    ))
                    or low_price > high_price
                    or open_price < low_price
                    or open_price > high_price
                    or close_price < low_price
                    or close_price > high_price
                ):
                    continue
                rows.append({
                    "timestamp": candle_time,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": candle.get("volume"),
                    "oi": 0,
                })
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise UpstoxFetchError(
                    f"Upstox returned malformed OHLC quote for {instrument_key}"
                ) from exc
        if rows:
            candles[instrument_key] = (
                pd.DataFrame(rows, columns=columns)
                .drop_duplicates(subset="timestamp", keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )

    return MarketQuoteOHLCBatch(candles, frozenset(observed), raw_id)


def historical_windows(
    from_date: date,
    to_date: date,
    *,
    available_from: date = HISTORY_AVAILABLE_FROM,
    chunk_days: int = HISTORICAL_CHUNK_DAYS,
) -> list[tuple[date, date]]:
    """Return chronological, inclusive ranges accepted by the V3 minute API."""

    if chunk_days < 1:
        raise ValueError("chunk_days must be positive")
    cursor = max(from_date, available_from)
    if cursor > to_date:
        return []
    windows = []
    while cursor <= to_date:
        end = min(cursor + timedelta(days=chunk_days - 1), to_date)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return windows


def fetch_intraday_candles(
    session: requests.Session,
    instrument_key: str,
    storage: Any,
    *,
    limiter: UpstoxRateLimiter | None = None,
) -> tuple[pd.DataFrame, Any]:
    """Fetch today's one-minute candles and archive the exact response."""

    encoded_key = quote(instrument_key, safe="")
    url = f"{BASE_URL}/v3/historical-candle/intraday/{encoded_key}/minutes/1"
    return _fetch_candles(
        session,
        url,
        instrument_key,
        storage,
        archive_source="upstox_intraday_candles",
        metadata={"instrument_key": instrument_key, "resolution": "1min"},
        limiter=limiter,
    )


def fetch_historical_candles(
    session: requests.Session,
    instrument_key: str,
    from_date: date,
    to_date: date,
    storage: Any,
    *,
    limiter: UpstoxRateLimiter | None = None,
) -> tuple[pd.DataFrame, Any]:
    """Fetch an inclusive historical one-minute range and archive the response."""

    encoded_key = quote(instrument_key, safe="")
    url = (
        f"{BASE_URL}/v3/historical-candle/{encoded_key}/minutes/1/"
        f"{to_date.isoformat()}/{from_date.isoformat()}"
    )
    return _fetch_candles(
        session,
        url,
        instrument_key,
        storage,
        archive_source="upstox_historical_candles",
        metadata={
            "instrument_key": instrument_key,
            "resolution": "1min",
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        },
        limiter=limiter,
    )


def _fetch_candles(
    session: requests.Session,
    url: str,
    instrument_key: str,
    storage: Any,
    *,
    archive_source: str,
    metadata: dict[str, str],
    limiter: UpstoxRateLimiter | None,
    timeout: float = 15,
    max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[pd.DataFrame, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        if limiter is not None:
            limiter.acquire()
        try:
            response = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < max_attempts:
                sleep(2 ** (attempt - 1))
                continue
            raise UpstoxFetchError(
                f"Upstox request failed for {instrument_key}: {exc}", retriable=True
            ) from exc

        raw_id = storage.archive_http_response(
            source=archive_source,
            source_url=url,
            response_body=response.content,
            status_code=response.status_code,
            response_headers=dict(response.headers),
            fetch_key=instrument_key,
            content_type=response.headers.get("Content-Type", "application/json"),
            metadata=metadata,
        )
        if response.status_code == 200:
            try:
                candles = response.json().get("data", {}).get("candles", [])
            except (TypeError, ValueError, AttributeError) as exc:
                raise UpstoxFetchError(
                    f"Upstox returned invalid candle JSON for {instrument_key}"
                ) from exc
            return _normalize_candles(candles, instrument_key), raw_id

        retriable = response.status_code == 429 or response.status_code >= 500
        last_error = UpstoxFetchError(
            f"Upstox returned HTTP {response.status_code} for {instrument_key}",
            retriable=retriable,
        )
        if not retriable or attempt == max_attempts:
            raise last_error
        sleep(_retry_delay(response, attempt))

    raise UpstoxFetchError(
        f"Upstox request failed for {instrument_key}: {last_error}", retriable=True
    )


def _normalize_candles(candles: Any, instrument_key: str) -> pd.DataFrame:
    columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    if not candles:
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.DataFrame(candles, columns=columns)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    except (TypeError, ValueError) as exc:
        raise UpstoxFetchError(
            f"Upstox returned malformed candles for {instrument_key}"
        ) from exc
    return frame.sort_values("timestamp").reset_index(drop=True)


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                return min(max(retry_at.timestamp() - time.time(), 0.0), 60.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2 ** (attempt - 1))
