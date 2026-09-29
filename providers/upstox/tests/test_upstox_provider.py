"""Upstox adapter: transport errors, planning, and an end-to-end run into ClickHouse sinks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from factorlab.ingest.bindings import Binding
from factorlab.ingest.datasets import (
    BarRequest,
    InstrumentRef,
    SeriesWindow,
)
from factorlab.ingest.engine import bar_request, reference_request, run_binding
from factorlab.ingest.errors import AuthRequired, PermanentError, RateLimited, TransientError
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.upstox.client import UpstoxClient
from factorlab.sources.upstox.settings import ALIAS_KIND, UpstoxSettings
from factorlab.sources.upstox.sources import (
    UpstoxBars,
    UpstoxContractBars,
    UpstoxContracts,
    UpstoxListings,
    historical_windows,
)
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.v2_india import V2IndiaStorage
from factorlab.testkit import conformance as kit
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 9, 24, 4, 1, 5, tzinfo=UTC)  # 09:31 IST
REL = InstrumentRef(ALIAS_KIND, "NSE_EQ|INE002A01018", "NSE", "RELIANCE", "IN",
                    isin="INE002A01018")


class Response:
    def __init__(self, status: int, body: bytes = b"{}", headers=None) -> None:
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def close(self):
        pass


class Session:
    """Routes GETs by URL substring; records the Authorization header of each call."""

    def __init__(self, routes: dict[str, list[Response] | Response]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str | None]] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, (headers or {}).get("Authorization")))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response.pop(0) if isinstance(response, list) else response
        raise AssertionError(f"unexpected URL {url}")


def client(session, tokens=("tok",)):
    tokens = list(tokens)

    def secret(name, default=""):
        return tokens[0] if len(tokens) == 1 else tokens.pop(0)

    return UpstoxClient(UpstoxSettings(), session=session, secret=secret, clock=lambda: NOW,
                        limiter=SlidingWindowLimiter([(1.0, 1000)]))


def test_client_maps_http_status_to_error_taxonomy():
    for status, error in ((403, AuthRequired), (429, RateLimited), (503, TransientError),
                          (404, PermanentError)):
        with pytest.raises(error):
            client(Session({"x": Response(status, headers={"Retry-After": "3"})})).get(
                "https://x", request_key="k")
    with pytest.raises(AuthRequired):
        client(Session({"x": Response(200)}), tokens=("",)).get("https://x", request_key="k")


def test_client_adopts_a_rotated_token_after_401():
    session = Session({"x": [Response(401), Response(200, b'{"ok": 1}')]})
    capture = client(session, tokens=("old", "new", "new")).get("https://x", request_key="k",
                                                                metadata={"a": 1})
    assert [auth for _, auth in session.calls] == ["Bearer old", "Bearer new"]
    assert (capture.body, capture.fetched_at, capture.metadata) == (b'{"ok": 1}', NOW, {"a": 1})


def test_rate_limit_retry_after_is_parsed():
    with pytest.raises(RateLimited) as info:
        client(Session({"x": Response(429, headers={"Retry-After": "7"})})).get(
            "https://x", request_key="k")
    assert info.value.retry_after == 7


def test_historical_windows_clamp_and_chunk():
    from datetime import date
    windows = historical_windows(date(2021, 12, 1), date(2022, 3, 5),
                                 available_from=date(2022, 1, 1), chunk_days=30)
    assert windows[0] == (date(2022, 1, 1), date(2022, 1, 30))
    assert windows[-1][1] == date(2022, 3, 5)
    assert historical_windows(date(2026, 9, 24), date(2026, 9, 23),
                              available_from=date(2022, 1, 1), chunk_days=30) == []


def test_candle_plan_splits_history_by_ist_day_and_adds_today_intraday():
    source = UpstoxBars(UpstoxSettings())
    start = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)  # 23 Sep 01:30 IST
    units = source.plan(BarRequest("IND", "1min", (SeriesWindow(REL, start, NOW),)))
    assert [u.name for u in units] == [
        "NSE_EQ|INE002A01018:2026-09-23..2026-09-23", "NSE_EQ|INE002A01018:intraday:2026-09-24"]
    history, today = units
    assert history.params == {"endpoint": "historical", "from": "2026-09-23", "to": "2026-09-23"}
    assert today.start == datetime(2026, 9, 23, 18, 30, tzinfo=UTC)  # IST midnight
    assert source._url(history).endswith("/minutes/1/2026-09-23/2026-09-23")
    assert "%7C" in source._url(today)  # the instrument key is URL-encoded


def test_quote_plan_batches_up_to_the_configured_size():
    refs = [InstrumentRef(ALIAS_KIND, f"NSE_EQ|INE00000{i:04d}", "NSE", f"S{i}", "IN")
            for i in range(230)]
    source = UpstoxBars(UpstoxSettings())
    units = source.plan(BarRequest("IND", "1min", tuple(
        SeriesWindow(r, NOW - timedelta(minutes=1), NOW) for r in refs), params={"mode": "quote"}))
    assert [len(u.instruments) for u in units] == [100, 100, 30]
    assert all(u.source_channel == "upstox:v3_quote_ohlc" for u in units)
    with pytest.raises(ValueError):
        source.plan(BarRequest("IND", "1min", (), params={"mode": "stream"}))


def _body(dataset: str, case: str) -> bytes:
    return {name: capture for name, capture, _ in kit.cases("upstox", dataset)}[case].body


def test_end_to_end_listings_contracts_bars_into_clickhouse_sinks():
    db = FakeClickHouse()
    sinks = ClickHouseSinks(V2IndiaStorage(db))
    master = Response(200, _body("ref.listings", "nse_master_slice"))
    session = Session({
        "assets.upstox.com": master,
        "/historical-candle/intraday/NSE_EQ%7CINE002A01018": Response(
            200, _body("market.bars", "intraday_with_repeat")),
        "/historical-candle/intraday/NSE_FO%7C61284": Response(
            200, _body("market.futures_contract_bars", "future_intraday")),
    })
    shared = client(session)

    def build(cls, **kw):
        return cls(UpstoxSettings(), client=shared, **kw)

    listings = Binding(dataset="ref.listings", market="IND", provider="upstox")
    contracts = Binding(dataset="ref.contracts", market="IND", provider="upstox")
    bars = Binding(dataset="market.bars", market="IND", provider="upstox", resolution="1min")
    fbars = Binding(dataset="market.futures_contract_bars", market="IND", provider="upstox",
                    resolution="1min")

    first = run_binding(listings, build(UpstoxListings), sinks, reference_request(listings))
    assert (first.status, first.rows_written) == ("success", 3)
    second = run_binding(contracts, build(UpstoxContracts), sinks, reference_request(contracts))
    # MAXHEALTH's underlying is not in the slice, so it is parked, not minted.
    assert (second.status, second.rows_written) == ("success", 1)
    assert any(row["alias_value"] == "NSE_FO|68672"
               for row in db.log["meta.unresolved_entities"])

    listing_ids = [row["listing_id"] for row in db.rows("ref.listings")
                   if row["trading_symbol"] == "RELIANCE"]
    request, unmapped = bar_request(bars, build(UpstoxBars), listing_ids, reference=sinks,
                                    checkpoints=sinks, now=NOW,
                                    default_lookback=timedelta(minutes=30))
    assert unmapped == () and request.series[0].instrument.alias_value == "NSE_EQ|INE002A01018"
    third = run_binding(bars, build(UpstoxBars), sinks, request)
    assert (third.status, third.rows_written) == ("success", 2)

    future_ref = InstrumentRef(ALIAS_KIND, "NSE_FO|61284", "NSE", "RELIANCE FUT 28 JUL 26", "IN")
    fourth = run_binding(fbars, build(UpstoxContractBars), sinks, BarRequest(
        "IND", "1min", (SeriesWindow(future_ref, NOW - timedelta(minutes=30), NOW),)))
    assert (fourth.status, fourth.rows_written) == ("success", 1)

    bar_rows = db.rows("market.bars")
    assert {row["source_channel"] for row in bar_rows} == {"upstox:v3_intraday"}
    assert {row["session"] for row in bar_rows} == {"regular"}
    assert all(row["listing_id"] == listing_ids[0] for row in bar_rows)
    [future_bar] = db.rows("market.futures_contract_bars")
    assert future_bar["underlying_listing_id"] == listing_ids[0] and future_bar["oi"] == 812000
    raw_channels = [row["source_channel"] for row in db.log["raw.archive"]]
    assert raw_channels.count("upstox:instruments") == 2
    runs = [row for row in db.log["meta.ingestion_runs"] if row["status"] != "running"]
    assert [row["pipeline"] for row in runs] == [
        "ref.listings:upstox", "ref.contracts:upstox", "market.bars.1min:upstox",
        "market.futures_contract_bars.1min:upstox"]
    # the watermark now resumes from the last stored minute
    marks = sinks.watermarks(listing_ids, dataset="market.bars", source="upstox",
                             resolution="1min")
    assert marks[listing_ids[0]] == datetime(2026, 9, 24, 3, 46, tzinfo=UTC)


def test_capture_metadata_is_enough_to_replay_bars():
    captured = {name: capture for name, capture, _ in kit.cases("upstox", "market.bars")}
    records = UpstoxBars(UpstoxSettings()).normalize(captured["historical_window"])
    assert [r.bar_time.isoformat() for r in records] == [
        "2026-09-23T03:45:00+00:00", "2026-09-23T03:46:00+00:00", "2026-09-23T09:59:00+00:00"]
    body = json.loads(captured["historical_window"].body)
    assert len(body["data"]["candles"]) == 4  # the inconsistent candle stays in raw, not in bars
