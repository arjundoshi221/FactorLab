"""Ticker-by-dataset matrix: bounded set-based reads and honest cell states."""

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.instrument_matrix import (
    CACHE,
    MatrixRepository,
    classify,
    expected_session,
    repository,
    trade_sql,
)

NOW = datetime(2026, 9, 24, 23, 0, tzinfo=UTC)  # Thu, after the XNYS close plus grace
A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")
SEC_B = UUID("00000000-0000-0000-0000-0000000000bb")


def listing(id, symbol, security=None, entity=None):
    return {
        "kind": "listing",
        "id": id,
        "listing_id": id,
        "security_id": security or id,
        "entity_id": entity or id,
        "symbol": symbol,
        "name": symbol,
        "country": "US",
        "exchange": "NYSE",
        "instrument_type": "common",
        "active": True,
        "configured": True,
        "expiry": None,
        "strike": None,
        "right": "",
    }


class Fake:
    """Answers each column group's query by a distinctive fragment and records every call."""

    def __init__(self, listings, fail=""):
        self.listings = listings
        self.fail = fail
        self.calls = []

    def __call__(self, sql, params, max_rows=20_000):
        self.calls.append((sql, params))
        if self.fail and self.fail in sql:
            raise RuntimeError("table missing")
        if "SELECT count() AS total" in sql:
            return [{"total": len(self.listings)}]
        if "SELECT * FROM (SELECT 'listing'" in sql:
            return self.listings
        if "SELECT DISTINCT country_code, source, resolution FROM meta.expected_series" in sql:
            return [
                {"country_code": "US", "source": "schwab", "resolution": "1min"},
                {"country_code": "US", "source": "schwab", "resolution": "daily"},
            ]
        if "SELECT DISTINCT listing_id, country_code, source, resolution" in sql:
            return [
                {"listing_id": A, "country_code": "US", "source": "schwab", "resolution": "1min"},
                {"listing_id": A, "country_code": "US", "source": "schwab", "resolution": "daily"},
                {"listing_id": B, "country_code": "US", "source": "schwab", "resolution": "1min"},
            ]
        if "FROM market.bars WHERE country_code IN" in sql:
            return [
                {
                    "listing_id": A,
                    "country_code": "US",
                    "source": "schwab",
                    "resolution": "1min",
                    "latest": date(2026, 9, 24),
                },
                {
                    "listing_id": A,
                    "country_code": "US",
                    "source": "schwab",
                    "resolution": "daily",
                    "latest": date(2026, 9, 21),
                },
                # B has daily bars it is not configured to collect, and no minute bars.
                {
                    "listing_id": B,
                    "country_code": "US",
                    "source": "schwab",
                    "resolution": "daily",
                    "latest": date(2026, 9, 1),
                },
            ]
        if "FROM fundamentals.filings" in sql:
            return [{"entity_id": A, "filings": 3, "latest": date(2026, 8, 1)}]
        if "FROM broker.positions_snapshot FINAL" in sql:
            return [
                {
                    "listing_id": None,
                    "security_id": SEC_B,
                    "broker_code": "ibkr",
                    "account_id": "U1",
                    "account_mode": "live",
                    "currency": "USD",
                    "snapshot_time": datetime(2026, 9, 24, 20, 31, tzinfo=UTC),
                    "quantity": 5,
                    "value": 500,
                },
                {
                    "listing_id": None,
                    "security_id": SEC_B,
                    "broker_code": "ibkr",
                    "account_id": "U1",
                    "account_mode": "live",
                    "currency": "EUR",
                    "snapshot_time": datetime(2026, 9, 24, 20, 31, tzinfo=UTC),
                    "quantity": 2,
                    "value": None,
                },
            ]
        if "FROM broker.executions GROUP BY" in sql:
            return [
                {
                    "listing_id": A,
                    "security_id": A,
                    "broker_code": "ibkr",
                    "account_id": "U1",
                    "orders": 4,
                    "taggable": 4,
                    "untagged": 1,
                    "latest": datetime(2026, 9, 24, 15, 0, tzinfo=UTC),
                },
            ]
        return []


def make(listings, fail=""):
    CACHE.clear()
    repo = MatrixRepository(object(), NOW)
    fake = Fake(listings, fail)
    repo.query = fake  # type: ignore[method-assign]
    return repo, fake


def page(repo, **kw):
    args = {
        "search": "",
        "country": "",
        "exchange": "",
        "scope": "relevant",
        "attention": "",
        "listing_id": None,
        "limit": 50,
        "offset": 0,
        "detail": False,
    }
    return repo.page(**{**args, **kw})


def test_expected_session_waits_for_the_close_plus_grace():
    assert expected_session("US", datetime(2026, 9, 24, 21, 0, tzinfo=UTC)) == date(2026, 9, 23)
    assert expected_session("US", NOW) == date(2026, 9, 24)
    assert expected_session("ZZ", NOW) is None


def test_classify_never_reports_missing_data_as_healthy():
    assert classify(None, date(2026, 9, 24), True) == "missing"
    assert classify(None, date(2026, 9, 24), False) == "not_configured"
    assert classify(date(2026, 9, 1), date(2026, 9, 24), False) == "stored"
    assert classify(date(2026, 9, 23), date(2026, 9, 24), True) == "stale"
    assert classify(date(2026, 9, 24), date(2026, 9, 24), True) == "ok"


def test_cells_cover_every_state_and_link_security_level_holdings():
    repo, _ = make([listing(A, "AAA"), listing(B, "BBB", security=SEC_B)])
    result = page(repo, detail=True)
    a, b = (row.cells for row in result.items)
    assert a["bars:US:schwab:1min"].state == "ok"
    assert a["bars:US:schwab:daily"].state == "stale"
    assert b["bars:US:schwab:1min"].state == "missing"
    assert b["bars:US:schwab:daily"].state == "stored"
    assert a["fundamentals"].state == "ok" and b["fundamentals"].state == "none"
    assert a["tags"].state == "needs_tags" and a["tags"].count == 1
    assert b["trades"].state == "none" and b["tags"].state == "none"
    # Security-level holdings belong to B; currencies stay separate rows, never summed.
    assert b["positions"].state == "ok" and b["positions"].count == 1
    assert {item["currency"] for item in b["positions"].items} == {"USD", "EUR"}
    assert a["positions"].state == "none"
    assert result.expected_sessions == {"US": date(2026, 9, 24)}
    assert result.unavailable == []


def test_political_cells_include_unresolved_filing_tickers_by_country():
    CACHE.clear()
    repo = MatrixRepository(object(), NOW)
    rows = [
        {
            "listing_id": A,
            "security_id": A,
            "entity_id": A,
            "ticker": None,
            "country": "US",
            "trades": 2,
            "latest": date(2026, 9, 1),
        },
        # Unresolved: matches A by ticker and country only; never matched to B's identity.
        {
            "listing_id": None,
            "security_id": None,
            "entity_id": None,
            "ticker": "AAA",
            "country": "US",
            "trades": 3,
            "latest": date(2026, 9, 10),
        },
        {
            "listing_id": None,
            "security_id": None,
            "entity_id": None,
            "ticker": "AAA",
            "country": "IN",
            "trades": 5,
            "latest": date(2026, 9, 12),
        },
    ]
    calls = []
    repo.query = lambda sql, params, max_rows=20_000: calls.append(params) or rows  # type: ignore[method-assign]
    from factorlab.components.api.instruments import Instrument

    cells = repo.political([Instrument(**listing(A, "AAA")), Instrument(**listing(B, "BBB"))])
    a, b = cells[str(A)]["political"], cells[str(B)]["political"]
    assert a.count == 5 and a.latest == date(2026, 9, 10)
    assert "filing ticker" in a.detail and "direct" in a.detail
    assert b.state == "none" and b.count == 0
    assert calls[0]["symbols"] == ["AAA", "BBB"]


def test_query_count_does_not_grow_with_the_page():
    repo, small = make([listing(A, "AAA")])
    page(repo)
    repo, large = make([listing(UUID(int=n), f"T{n}") for n in range(1, 101)])
    page(repo, limit=100)
    assert len(small.calls) == len(large.calls)
    bars = next(params for sql, params in large.calls if "FROM market.bars WHERE" in sql)
    assert len(bars["ids"]) == 100


def test_a_failed_column_group_is_unavailable_and_the_rest_still_render():
    repo, _ = make([listing(A, "AAA")], fail="FROM fundamentals.filings")
    result = page(repo)
    cells = result.items[0].cells
    assert cells["fundamentals"].state == "unavailable"
    assert cells["bars:US:schwab:1min"].state == "ok"
    assert result.unavailable == ["Fundamentals"]


def test_attention_filter_applies_before_pagination(monkeypatch):
    repo, fake = make([listing(A, "AAA")])
    monkeypatch.setattr(
        repo, "behind", lambda: {"sessions": {}, "lagging": {"US": {str(A)}, "IN": {str(B)}}}
    )
    page(repo, attention="bars", country="US")
    paged = next((sql, params) for sql, params in fake.calls if "LIMIT {limit:UInt16}" in sql)
    assert "id IN {focus:Array(UUID)}" in paged[0]
    assert paged[1]["focus"] == [str(A)]


def test_trade_sql_deduplicates_fills_by_version_and_excludes_contracts():
    sql = trade_sql(listing_filter="1")
    assert "argMax(perm_id, version)" in sql
    assert "broker.executions FINAL" not in sql
    assert "f.contract IS NULL" in sql
    # Tags count only for the account owner's profile.
    assert "w.user_id = a.user_id" in sql


def test_matrix_endpoint_validates_and_serves():
    repo, _ = make([listing(A, "AAA")])
    app.dependency_overrides[repository] = lambda: repo
    try:
        client = TestClient(app)
        response = client.get("/hub/api/v1/market-data/matrix", params={"limit": 10})
        assert response.status_code == 200
        body = response.json()
        assert [c["key"] for c in body["columns"]][:2] == [
            "bars:US:schwab:1min",
            "bars:US:schwab:daily",
        ]
        assert (
            client.get("/hub/api/v1/market-data/matrix", params={"limit": 101}).status_code == 422
        )
        assert (
            client.get("/hub/api/v1/market-data/matrix", params={"scope": "nope"}).status_code
            == 422
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
