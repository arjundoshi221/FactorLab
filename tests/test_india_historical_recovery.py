import uuid
from datetime import UTC, date, datetime

import pandas as pd
import scripts.factlab_india_clickhouse_5min as ingest


class FakeCalendar:
    def sessions_in_range(self, start, end):
        del start, end
        return pd.DatetimeIndex([pd.Timestamp("2026-08-17", tz="UTC")])

    def session_close(self, session):
        del session
        return pd.Timestamp("2026-08-17T10:00:00Z")


class FakeStorage:
    def __init__(self, latest=None):
        self.latest = latest or {}
        self.latest_before = None
        self.started = []
        self.finished = []
        self.writes = []

    def latest_candle_times(self, series, *, source="upstox", before=None):
        del series, source
        self.latest_before = before
        return self.latest

    def start_ingestion_run(self, **kwargs):
        self.started.append(kwargs)
        return "run"

    def finish_ingestion_run(self, run, **kwargs):
        self.finished.append((run, kwargs))

    def write_candles_1min(self, frame, **kwargs):
        self.writes.append((frame.copy(), kwargs))
        return len(frame)


def make_series():
    return ingest.CandleSeries(
        "NSE_EQ|INE002A01018",
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "RELIANCE",
    )


def test_recovery_resumes_inclusively_from_the_last_stored_session(monkeypatch):
    item = make_series()
    no_contract = uuid.UUID(int=0)
    storage = FakeStorage({
        (item.instrument_id, no_contract): datetime(2026, 8, 14, 9, 59, tzinfo=UTC)
    })
    requested_ranges = []

    def fetch(session, instrument_key, from_date, to_date, target_storage, *, limiter):
        del session, target_storage, limiter
        requested_ranges.append((instrument_key, from_date, to_date))
        frame = pd.DataFrame([{
            "timestamp": pd.Timestamp("2026-08-17T03:45:00Z"),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "oi": 0,
        }])
        return frame, "raw-id"

    monkeypatch.setattr(ingest, "fetch_historical_candles", fetch)

    completed = ingest.recover_historical(
        object(),
        [item],
        storage,
        FakeCalendar(),
        object(),
        universe="demo",
        now=datetime(2026, 8, 18, 4, 0, tzinfo=UTC),
    )

    assert completed is True
    assert storage.latest_before == datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    assert requested_ranges == [
        ("NSE_EQ|INE002A01018", date(2026, 8, 14), date(2026, 8, 17))
    ]
    assert storage.started[0]["pipeline"] == "india_historical_1min_recovery"
    assert storage.finished[0][1]["status"] == "success"
    assert storage.finished[0][1]["rows_written"] == 1


def test_recovery_skips_a_complete_previous_session(monkeypatch):
    item = make_series()
    storage = FakeStorage({
        (item.instrument_id, uuid.UUID(int=0)): datetime(2026, 8, 17, 9, 59, tzinfo=UTC)
    })
    monkeypatch.setattr(
        ingest,
        "fetch_historical_candles",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
    )

    completed = ingest.recover_historical(
        object(),
        [item],
        storage,
        FakeCalendar(),
        object(),
        universe="demo",
        now=datetime(2026, 8, 18, 4, 0, tzinfo=UTC),
    )

    assert completed is True
    assert storage.started == []


def test_never_seen_series_seeds_only_the_previous_session(monkeypatch):
    item = make_series()
    storage = FakeStorage()
    requested_ranges = []

    def fetch(session, instrument_key, from_date, to_date, target_storage, *, limiter):
        del session, instrument_key, target_storage, limiter
        requested_ranges.append((from_date, to_date))
        return pd.DataFrame(), "raw-id"

    monkeypatch.setattr(ingest, "fetch_historical_candles", fetch)

    assert ingest.recover_historical(
        object(),
        [item],
        storage,
        FakeCalendar(),
        object(),
        universe="demo",
        now=datetime(2026, 8, 18, 4, 0, tzinfo=UTC),
    )
    assert requested_ranges == [
        (date(2026, 8, 17), date(2026, 8, 17))
    ]
