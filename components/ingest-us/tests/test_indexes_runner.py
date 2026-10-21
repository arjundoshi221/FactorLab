"""The index runner replays history but writes only its window (ADR 0017)."""

from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from factorlab.components.ingest_us.indexes import IndexStore, next_run, run_once
from factorlab.indexes import IndexCatalog

A, B, C = (UUID(int=i) for i in range(1, 4))
CATALOG = IndexCatalog.model_validate(
    {
        "version": 1,
        "tracked": [{"symbol": "$SPX", "name": "S&P 500", "group": "index"}],
        "base_date": "2026-09-01",
        "base_value": 1000,
        "universe": "r3k_iwv",
        "coverage_gate": 0.98,
        "sectors": [
            {
                "sector_id": "45",
                "name": "Information Technology",
                "cw_etf": "XLK",
                "ew_etf": "RSPT",
            },
            {"sector_id": "40", "name": "Financials", "cw_etf": "XLF", "ew_etf": "RSPF"},
        ],
    }
)


class FakeStore:
    def __init__(self, sessions):
        self.sessions = sessions
        self.levels, self.weights, self.computations = [], [], []

    def universe_spans(self, universe):
        assert universe == "r3k_iwv"
        return [
            (A, date(2026, 10, 1), None),
            (B, date(2026, 10, 1), None),
            (C, date(2026, 10, 1), None),
        ]

    def sector_spans(self, classification):
        return [
            (A, "45", date(2026, 10, 1), None),
            (B, "45", date(2026, 10, 1), None),
            (C, "40", date(2026, 10, 1), None),
        ]

    def shares(self, listings):
        return {
            A: [(date(2026, 10, 1), 100.0)],
            B: [(date(2026, 10, 1), 100.0)],
            C: [(date(2026, 10, 1), 100.0)],
        }

    def closes(self, listings, start, end, *, source):
        assert source == "schwab"
        yield from ((d, c) for d, c in self.sessions if start <= d <= end)

    def start_computation(self, **kwargs):
        self.computations.append(("start", kwargs))
        return UUID(int=99)

    def finish_computation(self, **kwargs):
        self.computations.append(("finish", kwargs))

    def write_levels(self, rows):
        self.levels.extend(rows)

    def write_weights(self, rows):
        self.weights.extend(rows)


def sessions(days):
    start = date(2026, 9, 1)
    out, price = [], 100.0
    for i in range(days):
        day = start + timedelta(days=i)
        if day.weekday() < 5:
            price *= 1.01
            out.append((day, {A: price, B: 100.0, C: 50.0}))
    return out


def test_runner_writes_only_the_trailing_window_and_records_the_computation():
    store = FakeStore(sessions(30))
    summary = run_once(
        cast(IndexStore, store), CATALOG, source="schwab", today=date(2026, 9, 30), window_days=5
    )
    written_days = sorted({row["trade_date"] for row in store.levels})
    assert written_days and written_days[0] >= date(2026, 9, 25)
    assert {row["index_code"] for row in store.levels} == {
        "r3k_iwv_ew",
        "r3k_iwv_cw",
        "us_gics_45_ew",
        "us_gics_45_cw",
        "us_gics_40_ew",
        "us_gics_40_cw",
    }
    assert summary["latest"] == date(2026, 9, 30) and summary["incomplete"] == []
    assert [kind for kind, _ in store.computations] == ["start", "finish"]
    assert store.computations[1][1]["status"] == "success"
    # Tech EW: A gains 1% a day against flat B; levels compound from the base date.
    tech = [r for r in store.levels if r["index_code"] == "us_gics_45_ew"]
    assert tech[-1]["level"] > 1000 and all(r["complete"] for r in tech)
    day_rows = [
        w
        for w in store.weights
        if w["index_code"] == "us_gics_45_ew" and w["trade_date"] == tech[-1]["trade_date"]
    ]
    assert sum(w["contribution"] for w in day_rows) == pytest.approx(tech[-1]["ret_1d"])


def test_rebuild_writes_from_the_base_date():
    store = FakeStore(sessions(30))
    run_once(
        cast(IndexStore, store), CATALOG, source="schwab", today=date(2026, 9, 30), rebuild=True
    )
    assert min(row["trade_date"] for row in store.levels) == date(2026, 9, 1)


def test_empty_universe_is_an_error():
    store = FakeStore([])
    store.universe_spans = lambda universe: []
    with pytest.raises(ValueError, match="no published members"):
        run_once(cast(IndexStore, store), CATALOG, source="schwab", today=date(2026, 9, 30))


def test_daemon_schedule_skips_weekends():
    friday_late = datetime(2026, 10, 23, 23, 45, tzinfo=UTC)
    assert next_run(friday_late, datetime.strptime("23:30", "%H:%M").time()) == datetime(
        2026, 10, 26, 23, 30, tzinfo=UTC
    )
