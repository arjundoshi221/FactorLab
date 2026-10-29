"""Production inventory and recovery semantics through real sources and sinks."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID

import exchange_calendars as xcals
import requests

from factorlab.components.ingest_india import collector
from factorlab.ingest.bindings import BindingsFile, load_bindings
from factorlab.ingest.datasets import SeriesWindow
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.upstox.client import UpstoxClient
from factorlab.sources.upstox.settings import UpstoxSettings
from factorlab.storage.v2_india import V2IndiaStorage
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 10, 1, 4, 1, 5, tzinfo=UTC)


class Session:
    def __init__(self, master):
        self.master = master
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "json.gz" in url:
            body = self.master
        elif "market-quote/ohlc" in url:
            body = {
                "data": {
                    "NSE_EQ:ALPHA": {
                        "instrument_token": "NSE_EQ|INE000A01010",
                        "prev_ohlc": {
                            "ts": int(
                                (NOW - timedelta(minutes=1)).replace(second=0).timestamp() * 1000
                            ),
                            "open": 10,
                            "high": 12,
                            "low": 9,
                            "close": 11,
                            "volume": 4,
                        },
                    }
                }
            }
        else:
            raise AssertionError(url)
        response = Mock()
        response.content = json.dumps(body).encode()
        response.status_code = 200
        response.headers = {"Content-Type": "application/json"}
        return response

    def close(self):
        pass


def setup(monkeypatch):
    master: list[dict[str, object]] = [
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE000A01010",
            "isin": "INE000A01010",
            "trading_symbol": "ALPHA",
            "name": "Alpha",
        },
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE000B01010",
            "isin": "INE000B01010",
            "trading_symbol": "BETA",
            "name": "Beta",
        },
    ]
    # refresh() selects against the real IST date, so expiries follow it.
    today = datetime.now(collector.IST).date()
    for key, expiry in (
        ("nearest", today + timedelta(days=28)),
        ("distant", today + timedelta(days=56)),
        ("expired", today - timedelta(days=7)),
    ):
        master.append(
            {
                "segment": "NSE_FO",
                "instrument_type": "FUT",
                "instrument_key": f"NSE_FO|{key}",
                "trading_symbol": f"ALPHA {key}",
                "underlying_type": "EQUITY",
                "underlying_key": "NSE_EQ|INE000A01010",
                "underlying_symbol": "ALPHA",
                "expiry": int(
                    datetime.combine(expiry, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
                ),
                "lot_size": 10,
            }
        )
    configured = load_bindings()
    promoted = BindingsFile(
        version=1,
        bindings=tuple(
            b.model_copy(update={"role": "primary"}) if b.market == "IND" else b
            for b in configured.bindings
        ),
    )
    monkeypatch.setattr(collector, "load_bindings", lambda: promoted)
    session = Session(master)
    http = Mock(spec=requests.Session)
    http.get.side_effect = session.get
    sources = collector.Sources()
    sources.close()
    shared = UpstoxClient(
        UpstoxSettings(),
        session=http,
        secret=lambda *_: "test-token",
        clock=lambda: NOW,
        limiter=SlidingWindowLimiter([(1, 1000)]),
    )
    sources.clients["upstox"] = shared
    db = FakeClickHouse()
    storage = V2IndiaStorage(db)
    inventory = Mock(return_value=3)
    monkeypatch.setattr(storage, "sync_expected_india_series", inventory)
    sink = collector.IndiaSink(storage)
    series = collector.refresh(sink, sources, universe=collector.FULL_EQUITY_UNIVERSE)
    return sink, sources, db, series, inventory


def test_reference_inventory_selects_nearest_unexpired_futures(monkeypatch):
    sink, sources, db, series, inventory = setup(monkeypatch)
    assert {s.ref.alias_value for s in series} == {
        "NSE_EQ|INE000A01010",
        "NSE_EQ|INE000B01010",
        "NSE_FO|nearest",
    }
    futures = [s for s in series if s.contract_id]
    alpha = next(s for s in series if s.ref.trading_symbol == "ALPHA")
    assert futures[0].listing_id == alpha.listing_id
    assert len(inventory.call_args.args[0]) == 3
    assert inventory.call_args.kwargs == {"source": "upstox", "universe": "full_nse_eq"}
    # Listings and contracts read one master file; the identical capture is archived once.
    assert len(db.rows("raw.archive")) == 1
    assert db.rows("meta.ingestion_runs")[-1]["pipeline"] == "india_reference_premarket"
    sources.close()
    sink.close()


def test_missing_quote_keeps_valid_rows_and_reports_failure(monkeypatch):
    sink, sources, db, series, _ = setup(monkeypatch)
    equities = [s for s in series if s.contract_id is None]
    windows = [SeriesWindow(s.ref, NOW - timedelta(minutes=30), NOW) for s in equities]
    result = collector.collect_windows(
        sink,
        sources,
        series,
        windows,
        universe="full_nse_eq",
        pipeline="india_intraday_1min",
        quote=True,
        as_of=NOW,
    )
    assert result is not None and result.status == "failed"
    rows = db.rows("market.bars")
    assert len(rows) == 1
    run = db.rows("meta.ingestion_runs")[-1]
    assert run["status"] == "failed" and run["rows_written"] == 1
    assert "omitted 1 requested instruments" in run["error"]
    assert rows[0]["ingest_run_id"] == run["run_id"]
    assert rows[0]["raw_id"] == db.rows("raw.archive")[-1]["raw_id"]
    sources.close()
    sink.close()


def test_recovery_resumes_inclusively_and_skips_completed_series(monkeypatch):
    sink, sources, _, series, _ = setup(monkeypatch)
    calendar = xcals.get_calendar("XBOM")
    day, close = collector.previous_completed_session(calendar, NOW)
    first, second = [s for s in series if s.contract_id is None]
    last = datetime.combine(
        day - timedelta(days=2), datetime.min.time(), tzinfo=collector.IST
    ).astimezone(UTC) + timedelta(hours=10)
    monkeypatch.setattr(
        sink.storage,
        "latest_candle_times",
        lambda *_, **__: {
            (first.listing_id, UUID(int=0)): last,
            (second.listing_id, UUID(int=0)): close - timedelta(minutes=1),
        },
    )
    windows = collector.recovery_windows(sink, series, calendar, NOW)
    assert second.ref not in {w.instrument for w in windows}
    resumed = next(w for w in windows if w.instrument == first.ref)
    assert resumed.start.astimezone(collector.IST).date() == last.astimezone(collector.IST).date()
    unseen = next(w for w in windows if w.instrument.alias_value == "NSE_FO|nearest")
    assert unseen.start.astimezone(collector.IST).date() == day
    assert unseen.end == close
    sources.close()
    sink.close()


def test_full_universe_sweep_aligns_after_the_next_minute():
    assert collector.seconds_until_next_quote_sweep(now_epoch=20.0) == 45.0
    assert collector.seconds_until_next_quote_sweep(now_epoch=59.5) == 5.5
