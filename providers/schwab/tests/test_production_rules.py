"""Preserved production session and candle validation rules."""

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from factorlab.calendars.us import NY, bounds, latest_completed
from factorlab.ingest.errors import PartialNormalizationError
from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.normalize import normalize_pricehistory


def normalize(candles, resolution, *, now):
    return normalize_pricehistory(
        RawCapture(
            json.dumps({"candles": candles}).encode(),
            "test",
            "http",
            now,
            metadata={
                "resolution": resolution,
                "require_complete": True,
                "instruments": [
                    {"alias_value": "AAPL", "exchange_code": "XNAS", "trading_symbol": "AAPL"}
                ],
            },
        )
    )


market = SimpleNamespace(bounds=bounds, latest_completed=latest_completed, normalize=normalize)


def bar(stamp, **kwargs):
    return {
        "datetime": int(datetime.fromisoformat(stamp).timestamp() * 1000),
        "open": 10,
        "high": 12,
        "low": 9,
        "close": 11,
        "volume": 100,
        **kwargs,
    }


@pytest.mark.parametrize(
    "day,opened,closed",
    [
        (date(2026, 3, 6), 14, 21),
        (date(2026, 3, 9), 13, 20),
        (date(2026, 11, 27), 14, 18),
    ],
)
def test_dst_and_early_close(day, opened, closed):
    start, end = market.bounds(day)
    assert (start.hour, start.minute, end.hour) == (opened, 30, closed)


def test_holiday_and_postclose_grace():
    assert market.bounds(date(2026, 9, 7)) is None
    assert market.latest_completed(datetime(2026, 9, 8, 20, 29, tzinfo=UTC)) == date(2026, 9, 4)
    assert market.latest_completed(datetime(2026, 9, 8, 20, 30, tzinfo=UTC)) == date(2026, 9, 8)


def test_minute_filters_extended_hours_incomplete_and_duplicates():
    frame = market.normalize(
        [
            bar("2026-09-04T13:29:00+00:00"),
            bar("2026-09-04T13:30:00+00:00"),
            bar("2026-09-04T13:30:00+00:00", close=10),
            bar("2026-09-04T13:31:00+00:00"),
            bar("2026-09-04T20:00:00+00:00"),
        ],
        "1min",
        now=datetime(2026, 9, 4, 13, 31, 30, tzinfo=UTC),
    )
    assert len(frame) == 1
    assert frame[0].close == 10


def test_daily_uses_new_york_date_and_excludes_incomplete_session():
    frame = market.normalize(
        [bar("2026-09-04T05:00:00+00:00"), bar("2026-09-08T05:00:00+00:00")],
        "daily",
        now=datetime(2026, 9, 8, 15, tzinfo=UTC),
    )
    assert [row.bar_time.astimezone(NY).date() for row in frame] == [date(2026, 9, 4)]


def test_daily_skips_provider_history_before_calendar_start():
    frame = market.normalize(
        [
            bar("1969-12-31T05:00:00+00:00"),
            bar("1970-01-02T05:00:00+00:00"),
        ],
        "daily",
        now=datetime(1970, 1, 5, tzinfo=UTC),
    )
    assert [row.bar_time.astimezone(NY).date() for row in frame] == [date(1970, 1, 2)]


@pytest.mark.parametrize(
    "update", [{"high": 8}, {"volume": -1}, {"volume": 1.2}, {"close": float("nan")}]
)
def test_invalid_ohlcv_rejected(update):
    with pytest.raises(PartialNormalizationError):
        market.normalize(
            [bar("2026-09-04T13:30:00+00:00", **update)],
            "1min",
            now=datetime(2026, 9, 4, 14, tzinfo=UTC),
        )
