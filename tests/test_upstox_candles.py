from datetime import date

import pandas as pd

from factorlab.countries.in_.equities.upstox.candles import (
    UpstoxRateLimiter,
    _normalize_market_quote_ohlc,
    fetch_historical_candles,
    fetch_market_quote_ohlc,
    historical_windows,
    instrument_key_batches,
)


class FakeResponse:
    def __init__(self, status_code, payload, *, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = b'{"fixture":true}'
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, timeout):
        self.requests.append((url, timeout))
        return self.responses.pop(0)


class FakeStorage:
    def __init__(self):
        self.archives = []

    def archive_http_response(self, **kwargs):
        self.archives.append(kwargs)
        return f"raw-{len(self.archives)}"


def test_historical_windows_are_chronological_and_clamped_to_retention():
    assert historical_windows(date(2021, 12, 1), date(2022, 2, 2)) == [
        (date(2022, 1, 1), date(2022, 1, 30)),
        (date(2022, 1, 31), date(2022, 2, 2)),
    ]


def test_historical_fetch_uses_v3_date_order_archives_and_sorts():
    response = FakeResponse(
        200,
        {
            "data": {
                "candles": [
                    ["2026-08-17T09:16:00+05:30", 2, 3, 1, 2, 20, 0],
                    ["2026-08-17T09:15:00+05:30", 1, 2, 1, 2, 10, 0],
                ]
            }
        },
    )
    session = FakeSession([response])
    storage = FakeStorage()

    frame, raw_id = fetch_historical_candles(
        session,
        "NSE_EQ|INE002A01018",
        date(2026, 8, 14),
        date(2026, 8, 17),
        storage,
    )

    assert session.requests[0][0].endswith(
        "/NSE_EQ%7CINE002A01018/minutes/1/2026-08-17/2026-08-14"
    )
    assert raw_id == "raw-1"
    assert storage.archives[0]["source"] == "upstox_historical_candles"
    assert frame["timestamp"].tolist() == sorted(frame["timestamp"].tolist())
    assert frame["timestamp"].dt.tz is not None


def test_historical_fetch_retries_429_and_archives_each_response():
    session = FakeSession([
        FakeResponse(429, {}, headers={"Retry-After": "0"}),
        FakeResponse(200, {"data": {"candles": []}}),
    ])
    storage = FakeStorage()

    frame, raw_id = fetch_historical_candles(
        session,
        "NSE_EQ|TEST",
        date(2026, 8, 17),
        date(2026, 8, 17),
        storage,
    )

    assert frame.empty
    assert raw_id == "raw-2"
    assert len(storage.archives) == 2


def test_rate_limiter_waits_at_the_per_second_boundary():
    now = [100.0]
    waits = []

    def clock():
        return now[0]

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    limiter = UpstoxRateLimiter(clock=clock, sleep=sleep)
    for _ in range(51):
        limiter.acquire()

    assert waits == [1.0]


def test_instrument_keys_are_split_into_stable_quote_batches():
    assert instrument_key_batches(["a", "b", "c", "d", "e"], batch_size=2) == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]


def test_market_quote_ohlc_fetch_archives_and_normalizes_previous_minute():
    response = FakeResponse(
        200,
        {
            "status": "success",
            "data": {
                "NSE_EQ:RELIANCE": {
                    "instrument_token": "NSE_EQ|INE002A01018",
                    "prev_ohlc": {
                        "open": 1400.0,
                        "high": 1401.5,
                        "low": 1399.0,
                        "close": 1401.0,
                        "volume": 1234,
                        "ts": 1789011960000,
                    },
                },
                "NSE_EQ:NOTRADED": {
                    "instrument_token": "NSE_EQ|INE000000001",
                    "live_ohlc": None,
                },
                "NSE_EQ:INVALID": {
                    "instrument_token": "NSE_EQ|INE000000002",
                    "prev_ohlc": {
                        "open": 110,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 5,
                        "ts": 1789011960000,
                    },
                },
            },
        },
    )
    session = FakeSession([response])
    storage = FakeStorage()

    result = fetch_market_quote_ohlc(
        session,
        ["NSE_EQ|INE002A01018", "NSE_EQ|INE000000001", "NSE_EQ|INE000000002"],
        storage,
    )

    requested_url = session.requests[0][0]
    assert requested_url.startswith("https://api.upstox.com/v3/market-quote/ohlc?")
    assert "interval=I1" in requested_url
    assert result.raw_id == "raw-1"
    assert result.observed_keys == {
        "NSE_EQ|INE002A01018",
        "NSE_EQ|INE000000001",
        "NSE_EQ|INE000000002",
    }
    assert set(result.candles) == {"NSE_EQ|INE002A01018"}
    frame = result.candles["NSE_EQ|INE002A01018"]
    assert frame.iloc[0]["close"] == 1401.0
    assert frame.iloc[0]["volume"] == 1234
    assert str(frame.iloc[0]["timestamp"].tz) == "UTC"
    assert storage.archives[0]["source"] == "upstox_market_quote_ohlc_v3"
    assert storage.archives[0]["metadata"]["instrument_count"] == 3


def test_market_quote_only_accepts_live_candle_after_its_minute_is_complete():
    def candle(timestamp):
        return {
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "volume": 50,
            "ts": int(pd.Timestamp(timestamp).timestamp() * 1000),
        }

    data = {
        "NSE_EQ:CLOSED": {
            "instrument_token": "NSE_EQ|CLOSED",
            "prev_ohlc": candle("2026-09-15T09:58:00Z"),
            "live_ohlc": candle("2026-09-15T09:59:00Z"),
        },
        "NSE_EQ:LIVE": {
            "instrument_token": "NSE_EQ|LIVE",
            "prev_ohlc": candle("2026-09-15T09:59:00Z"),
            "live_ohlc": candle("2026-09-15T10:00:00Z"),
        },
    }

    result = _normalize_market_quote_ohlc(
        data,
        "raw-id",
        now=pd.Timestamp("2026-09-15T10:00:05Z"),
    )

    assert result.candles["NSE_EQ|CLOSED"]["timestamp"].tolist() == [
        pd.Timestamp("2026-09-15T09:58:00Z"),
        pd.Timestamp("2026-09-15T09:59:00Z"),
    ]
    assert result.candles["NSE_EQ|LIVE"]["timestamp"].tolist() == [
        pd.Timestamp("2026-09-15T09:59:00Z"),
    ]
