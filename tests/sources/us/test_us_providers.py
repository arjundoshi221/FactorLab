"""EODHD + Schwab adapters: transport safety, planning, and a multi-provider US run."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from factorlab.shared.ingest.bindings import Binding
from factorlab.shared.ingest.datasets import BarRequest, InstrumentRef, SeriesWindow
from factorlab.shared.ingest.engine import bar_request, reference_request, run_binding
from factorlab.shared.ingest.errors import AuthRequired, QuotaExhausted
from factorlab.shared.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.eodhd.client import EodhdClient
from factorlab.sources.eodhd.settings import EodhdSettings
from factorlab.sources.eodhd.sources import EodhdDailyBars, EodhdListings
from factorlab.sources.schwab.settings import SchwabSettings
from factorlab.sources.schwab.sources import SchwabBars, SchwabListings
from factorlab.sources.schwab.transport import SchwabTransport
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.v2_us import V2USStorage
from tests.contracts import kit
from tests.contracts.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
FAST = SlidingWindowLimiter([(1.0, 10_000)])
US_EXCHANGES = {"XNAS": ("XNAS", "US", "USD"), "XNYS": ("XNYS", "US", "USD"),
                "XASE": ("XASE", "US", "USD"), "ARCX": ("ARCX", "US", "USD")}


class Response:
    def __init__(self, status=200, body=b"[]"):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": "application/json", "Set-Cookie": "session=secret"}


class Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        for fragment, response in self.routes.items():
            if fragment in url and (not isinstance(response, dict)):
                return response
            if fragment in url:
                key = (params or {}).get("symbol") or (params or {}).get("date") or ""
                return response.get(key, Response(200, b'{"instruments": []}'))
        raise AssertionError(f"unexpected URL {url}")


def body(provider, dataset, case):
    return {name: c for name, c, _ in kit.cases(provider, dataset)}[case].body


def eodhd_client(session, key="k3y"):
    return EodhdClient(EodhdSettings(), session=session, limiter=FAST, clock=lambda: NOW,
                       secret=lambda name, default="": key)


def schwab_transport(session, expiry="2026-09-24T02:00:00+00:00"):
    secrets = {"SCHWAB_ACCESS_TOKEN": "tok", "SCHWAB_ACCESS_TOKEN.expires_at": expiry}
    return SchwabTransport(SchwabSettings(), session=session, limiter=FAST, clock=lambda: NOW,
                           secret=lambda name, default="": secrets.get(name, default))


def test_eodhd_key_is_sent_but_never_archived():
    session = Session({"/eod/": Response(200, b"[]")})
    capture = eodhd_client(session).get("/eod/AAPL.US", {"from": "2026-09-01"}, request_key="k")
    assert session.calls[0][1]["api_token"] == "k3y"
    assert "k3y" not in capture.source_url and "k3y" not in str(capture.metadata)
    assert "Set-Cookie" not in capture.headers


def test_eodhd_quota_and_missing_key_end_the_run():
    with pytest.raises(QuotaExhausted):
        eodhd_client(Session({"/eod/": Response(402)})).get("/eod/X.US", request_key="k")
    with pytest.raises(AuthRequired):
        eodhd_client(Session({}), key="").get("/eod/X.US", request_key="k")


def test_schwab_expired_token_is_auth_required():
    with pytest.raises(AuthRequired):
        schwab_transport(Session({}), expiry="2026-09-24T00:00:00+00:00").get(
            "/pricehistory", {}, request_key="k")
    session = Session({"/pricehistory": Response(200, b'{"candles": []}')})
    schwab_transport(session).get("/pricehistory", {"symbol": "AAPL"}, request_key="k")
    assert session.calls[0][2]["Authorization"] == "Bearer tok"


AAPL_E = InstrumentRef("eodhd_symbol", "AAPL.US", "XNAS", "AAPL", "US", isin="US0378331005")
AAPL_S = InstrumentRef("schwab_symbol", "AAPL", "XNAS", "AAPL", "US", isin="US0378331005")


def test_eodhd_bulk_plan_skips_non_sessions_and_caps_days():
    window = SeriesWindow(AAPL_E, datetime(2026, 9, 1, tzinfo=UTC), NOW)
    units = EodhdDailyBars(EodhdSettings()).plan(
        BarRequest("USA", "daily", (window,), params={"mode": "bulk"}))
    # bulk_max_days=5 ending Wed 23 Sep (NY) -> Sat/Sun skipped
    assert [u.params["date"] for u in units] == ["2026-09-21", "2026-09-22", "2026-09-23"]


def test_schwab_minute_plan_clamps_lookback_and_chunks():
    start, end = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 23, 20, tzinfo=UTC)
    units = SchwabBars(SchwabSettings()).plan(
        BarRequest("USA", "1min", (SeriesWindow(AAPL_S, start, end),)))
    assert units[0].start == end - timedelta(days=48)
    assert all(u.end - u.start <= timedelta(days=10) for u in units)
    assert units[-1].end == end and len(units) == 5
    daily = SchwabBars(SchwabSettings()).plan(
        BarRequest("USA", "daily", (SeriesWindow(AAPL_S, start, end),)))
    assert len(daily) == 1


def test_us_multi_provider_run_shares_listings_and_keys():
    db = FakeClickHouse(exchanges=US_EXCHANGES)
    sinks = ClickHouseSinks(V2USStorage(db))
    e_session = Session({
        "/exchange-symbol-list/": Response(200, body("eodhd", "ref.listings", "us_symbol_list")),
        "/eod/": Response(200, body("eodhd", "market.bars", "eod_history")),
    })
    s_session = Session({
        "/instruments": {"BRK/B": Response(200, body("schwab", "ref.listings",
                                                     "instrument_brk_b"))},
        "/pricehistory": Response(200, body("schwab", "market.bars", "pricehistory_daily")),
    })
    e_client, s_transport = eodhd_client(e_session), schwab_transport(s_session)

    listings_e = Binding(dataset="ref.listings", market="USA", provider="eodhd")
    assert run_binding(listings_e, EodhdListings(EodhdSettings(), client=e_client), sinks,
                       reference_request(listings_e)).rows_written == 3
    ids = {row["trading_symbol"]: row["listing_id"] for row in db.rows("ref.listings")}

    # Schwab (secondary) looks up canonical listings one by one and only attaches aliases.
    listings_s = Binding(dataset="ref.listings", market="USA", provider="schwab",
                         role="secondary")
    request = reference_request(listings_s, [ids["BRK-B"], ids["AAPL"]], reference=sinks)
    assert [r.trading_symbol for r in request.instruments] == ["BRK-B", "AAPL"]
    summary = run_binding(listings_s, SchwabListings(SchwabSettings(), transport=s_transport),
                          sinks, request)
    assert summary.rows_written == 1  # BRK/B found (via CUSIP-derived ISIN); AAPL lookup empty
    assert len(db.rows("ref.listings")) == 3  # nothing minted or rewritten
    schwab_alias = [a for a in db.rows("ref.identifier_aliases")
                    if a["alias_kind"] == "schwab_symbol"]
    assert [(a["alias_value"], a["target_id"], a["confidence"]) for a in schwab_alias] == [
        ("BRK/B", ids["BRK-B"], "high")]

    # EODHD daily bars as primary; Schwab daily bars as shadow for the same listing.
    bars_e = Binding(dataset="market.bars", market="USA", resolution="daily", provider="eodhd")
    req_e, unmapped = bar_request(bars_e, EodhdDailyBars(EodhdSettings()), [ids["AAPL"]],
                                  reference=sinks, checkpoints=sinks, now=NOW,
                                  default_lookback=timedelta(days=4))
    assert unmapped == ()
    assert run_binding(bars_e, EodhdDailyBars(EodhdSettings(), client=e_client), sinks,
                       req_e).rows_written == 2
    bars_s = Binding(dataset="market.bars", market="USA", resolution="daily",
                     provider="schwab", role="shadow")
    shadow = run_binding(bars_s, SchwabBars(SchwabSettings(), transport=s_transport), sinks,
                         BarRequest("USA", "daily", (SeriesWindow(
                             AAPL_S, NOW - timedelta(days=4), NOW),)))
    assert (shadow.source, shadow.rows_written) == ("schwab:shadow", 3)

    rows = db.log["market.bars"]
    assert {r["listing_id"] for r in rows} == {ids["AAPL"]}
    by_source = {}
    for r in rows:
        by_source.setdefault(r["source"], set()).add(r["bar_time"])
    # Both vendors' daily bars key at New York midnight, so they line up per day.
    assert by_source["eodhd"] <= by_source["schwab:shadow"]
    assert datetime(2026, 9, 21, 4, 0, tzinfo=UTC) in by_source["eodhd"]
    assert {r["trade_date"] for r in rows} == {date(2026, 9, 21), date(2026, 9, 22),
                                               date(2026, 9, 23)}
    assert all(r["country_code"] == "US" for r in rows)
    # the shadow never touched the incumbent's watermark
    marks = sinks.watermarks([ids["AAPL"]], dataset="market.bars", source="eodhd",
                             resolution="daily")
    assert marks[ids["AAPL"]] == datetime(2026, 9, 22, 4, 0, tzinfo=UTC)


class GithubResponse(Response):
    def __init__(self, body, url):
        super().__init__(200, body)
        self.url = url


class GithubSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, timeout=None, allow_redirects=True):
        return self.response


def test_universe_membership_drives_the_bar_universe():
    from factorlab.sources.eodhd.sources import EodhdUniverse
    from factorlab.sources.github_csv.sources import GithubCsvSettings, GithubCsvUniverse

    db = FakeClickHouse(exchanges=US_EXCHANGES)
    sinks = ClickHouseSinks(V2USStorage(db))
    e_session = Session({
        "/exchange-symbol-list/": Response(200, body("eodhd", "ref.listings", "us_symbol_list")),
        "/fundamentals/": Response(200, body("eodhd", "ref.universe_membership",
                                             "sp500_components")),
        "/eod/": Response(200, body("eodhd", "market.bars", "eod_history")),
    })
    e_client = eodhd_client(e_session)
    listings = Binding(dataset="ref.listings", market="USA", provider="eodhd")
    run_binding(listings, EodhdListings(EodhdSettings(), client=e_client), sinks,
                reference_request(listings))

    csv_capture = {n: c for n, c, _ in kit.cases("github_csv", "ref.universe_membership")}[
        "sp500_constituents"]
    settings = GithubCsvSettings.model_validate({"indexes": {"sp500": {
        "url": csv_capture.source_url, "symbol_column": "Symbol", "minimum_constituents": 3,
        "name": "S&P 500"}}})
    github = GithubCsvUniverse(settings, session=GithubSession(
        GithubResponse(csv_capture.body, csv_capture.source_url)), clock=lambda: NOW)
    universe = Binding(dataset="ref.universe_membership", market="USA", provider="github_csv")
    summary = run_binding(universe, github, sinks, reference_request(universe))
    # AAPL and BRK-B resolve by country-scoped ticker; MSFT is not in the listing slice.
    assert (summary.status, summary.rows_written) == ("success", 2)
    assert any(row["alias_value"] == "US:MSFT"
               for row in db.log["meta.unresolved_entities"])
    assert db.rows("ref.universes")[0]["name"] == "S&P 500"

    # EODHD's own S&P components run as shadow: resolved and reported, nothing written.
    eodhd_universe = Binding(dataset="ref.universe_membership", market="USA", provider="eodhd",
                             role="shadow")
    compare = run_binding(eodhd_universe, EodhdUniverse(EodhdSettings.model_validate({
        "universes": {"sp500": {"symbol": "GSPC.INDX", "minimum_constituents": 3}}}),
        client=e_client), sinks, reference_request(eodhd_universe))
    assert (compare.source, compare.rows_written) == ("eodhd:shadow", 0)
    assert len(db.rows("ref.universe_membership")) == 2

    members = sinks.universe_members(["sp500"], as_of=NOW.date())
    ids = {row["listing_id"]: row["trading_symbol"] for row in db.rows("ref.listings")}
    assert sorted(ids[m] for m in members) == ["AAPL", "BRK-B"]
    bars = Binding(dataset="market.bars", market="USA", resolution="daily", provider="eodhd")
    request, unmapped = bar_request(bars, EodhdDailyBars(EodhdSettings()), members,
                                    reference=sinks, checkpoints=sinks, now=NOW,
                                    default_lookback=timedelta(days=4))
    assert unmapped == ()
    assert sorted(w.instrument.alias_value for w in request.series) == ["AAPL.US", "BRK-B.US"]
