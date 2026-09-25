"""Read-only Schwab market data, regular-session normalization, and bounded retries."""
from __future__ import annotations

import math
import time
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import requests

from factorlab.core.secrets import get_secret
from factorlab.sources.schwab.client import get_session

NY = ZoneInfo("America/New_York")
BASE_URL = "https://api.schwabapi.com/marketdata/v1"


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar("XNYS", start="1970-01-01", end=f"{datetime.now(UTC).year + 2}-12-31")


def bounds(day: date):
    cal = calendar()
    if not cal.is_session(day.isoformat()):
        return None
    return (cal.session_open(day.isoformat()).to_pydatetime(),
            cal.session_close(day.isoformat()).to_pydatetime())


def latest_completed(now: datetime, *, grace_minutes: int = 30) -> date:
    cal = calendar()
    day = now.astimezone(NY).date()
    for session in reversed(cal.sessions_in_range(day - timedelta(days=20), day)):
        if cal.session_close(session).to_pydatetime() + timedelta(minutes=grace_minutes) <= now:
            return session.date()
    raise RuntimeError("No completed US session found")


def token_ready(now: datetime | None = None) -> bool:
    if not get_secret("SCHWAB_ACCESS_TOKEN", ""):
        return False
    expiry = get_secret("SCHWAB_ACCESS_TOKEN.expires_at", "")
    if not expiry:
        return False
    try:
        return datetime.fromisoformat(expiry.replace("Z", "+00:00")) > (now or datetime.now(UTC))
    except (ValueError, TypeError):
        return False


class AuthRequired(RuntimeError):
    pass


class MarketClient:
    def __init__(self, storage, *, session=None, sleep=time.sleep, clock=time.monotonic):
        self.storage = storage
        self.session = session
        self.sleep = sleep
        self.clock = clock
        self.last_request = -math.inf

    def get(self, endpoint: str, params: dict):
        for attempt in range(5):
            if not token_ready():
                raise AuthRequired("Schwab reauthentication required")
            if self.session is None:
                self.session = get_session()
            self.sleep(max(0, 1 - (self.clock() - self.last_request)))
            self.last_request = self.clock()
            try:
                response = self.session.get(BASE_URL + endpoint, params=params, timeout=30)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 4:
                    raise RuntimeError("Schwab network request failed after five attempts") from exc
                self.sleep(2 ** attempt)
                continue
            raw_id = self.storage.archive_http_response(
                source="schwab", source_url=BASE_URL + endpoint, response_body=response.content,
                status_code=response.status_code, content_type="application/json",
                metadata={"params": params},
            )
            if response.status_code in (401, 403):
                raise AuthRequired(f"Schwab access unavailable (HTTP {response.status_code})")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    raise RuntimeError(f"Schwab HTTP {response.status_code} after five attempts")
                delay = 2 ** attempt
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        delay = max(delay, float(retry))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                self.sleep(delay)
                continue
            if not response.ok:
                raise RuntimeError(f"Schwab rejected {endpoint}: HTTP {response.status_code}")
            return response.json(), raw_id
        raise RuntimeError("Schwab retry budget exhausted")

    def instrument(self, symbol):
        payload, raw_id = self.get("/instruments", {"symbol": symbol, "projection": "symbol-search"})
        exact = [r for r in payload.get("instruments", []) if r.get("symbol") == symbol]
        if len(exact) != 1 or exact[0].get("assetType") not in ("EQUITY", "ETF"):
            raise ValueError(f"No unique equity/ETF match for {symbol}")
        return exact[0], raw_id

    def candles(self, symbol, resolution, start, end):
        payload, raw_id = self.get("/pricehistory", {
            "symbol": symbol, "periodType": "year" if resolution == "daily" else "day",
            "frequencyType": "daily" if resolution == "daily" else "minute", "frequency": 1,
            "startDate": int(start.timestamp() * 1000), "endDate": int(end.timestamp() * 1000),
            "needExtendedHoursData": "false",
        })
        if not isinstance(payload.get("candles"), list):
            raise ValueError("Schwab response has no candle array")
        return normalize(payload["candles"], resolution, now=end), raw_id


def normalize(records, resolution, *, now):
    """Daily timestamps denote US session dates; minute timestamps denote bar starts."""
    rows = []
    invalid_candles = 0
    sessions = {}
    first_session = calendar().first_session.date()
    last_session = calendar().last_session.date()
    for record in records:
        stamp = datetime.fromtimestamp(record["datetime"] / 1000, UTC)
        day = stamp.astimezone(NY).date()
        if not first_session <= day <= last_session:
            continue
        if day not in sessions:
            sessions[day] = bounds(day)
        session = sessions[day]
        if session is None:
            continue
        opened, closed = session
        if resolution == "1min":
            if not (opened <= stamp < closed) or stamp + timedelta(minutes=1) > now:
                continue
            if stamp.second or stamp.microsecond:
                raise ValueError("Minute bar is not aligned to a minute")
        elif closed > now:
            continue
        values = [float(record[k]) for k in ("open", "high", "low", "close")]
        volume = record["volume"]
        if (not all(math.isfinite(v) and v >= 0 for v in values)
                or values[1] < max(values[0], values[2], values[3])
                or values[2] > min(values[0], values[1], values[3])
                or not isinstance(volume, (int, float)) or not math.isfinite(volume)
                or volume < 0 or int(volume) != volume):
            if resolution != "daily":
                raise ValueError("Invalid OHLCV candle")
            invalid_candles += 1
            continue
        rows.append({"timestamp": stamp, "trade_date": day,
                     **{k: record[k] for k in ("open", "high", "low", "close", "volume")}})
    key = "trade_date" if resolution == "daily" else "timestamp"
    if not rows and invalid_candles:
        raise ValueError("Invalid OHLCV candle")
    frame = (pd.DataFrame(rows).drop_duplicates(key, keep="last").sort_values(key)
             if rows else pd.DataFrame())
    frame.attrs["invalid_candles"] = invalid_candles
    return frame
