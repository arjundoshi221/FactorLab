"""P5 political datasets: legislators -> filings -> trades through the engine into ClickHouse sinks."""

from __future__ import annotations

from datetime import UTC, date, datetime

from factorlab.ingest.bindings import Binding
from factorlab.ingest.datasets import InstrumentRecord, InstrumentRef, ReferenceRequest
from factorlab.ingest.datasets.political import FilingsRequest
from factorlab.ingest.engine import reference_request, run_binding
from factorlab.ingest.identifiers import legislator_name_key
from factorlab.ingest.memory import InMemorySink
from factorlab.ingest.provider import ingestion_run
from factorlab.sources.congress_legislators.sources import (
    CongressLegislators,
    CongressLegislatorsSettings,
    normalize_role,
)
from factorlab.sources.house_clerk.sources import (
    HouseClerkFilings,
    HouseClerkSettings,
    HouseClerkTrades,
    canonical_ticker,
)
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.v2_us import V2USStorage
from factorlab.testkit import conformance as kit
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 9, 24, 2, 15, tzinfo=UTC)
US = {"XNAS": ("XNAS", "US", "USD"), "XNYS": ("XNYS", "US", "USD")}


class Response:
    def __init__(self, body, status=200):
        self.content = body
        self.status_code = status
        self.headers = {"Content-Type": "application/octet-stream"}


class Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for fragment, body in self.routes.items():
            if fragment in url:
                return Response(body)
        raise AssertionError(url)


def body(provider, dataset, case):
    return {n: c for n, c, _ in kit.cases(provider, dataset)}[case].body


def test_name_key_and_role_rules_match_the_legacy_resolver():
    assert legislator_name_key("Ada M.", "Example") == "ada|example"
    assert legislator_name_key("", "Example") is None
    assert [normalize_role(t) for t in ("Chairman", "Vice Chair", "Ranking Member", None)] == [
        "chair",
        "vice_chair",
        "ranking_member",
        "member",
    ]
    assert canonical_ticker("BRK.B") == "BRK-B" and canonical_ticker("not a ticker!") is None


def _seed_listings(sinks):
    records = [
        InstrumentRecord(
            ref=InstrumentRef("eodhd_symbol", f"{s}.US", ex, s, "US"),
            name=s,
            product_type="common",
            currency="USD",
            first_traded=date(1996, 1, 1),
        )
        for s, ex in (("AAPL", "XNAS"), ("AMZN", "XNAS"), ("BRK-B", "XNYS"))
    ]
    with ingestion_run(sinks, pipeline="seed", source="eodhd", market_code="USA") as ctx:
        sinks.upsert_instruments(
            records,
            provenance=ctx.provenance(source_channel="eodhd:seed", raw_id=None, as_of_time=NOW),
        )
        ctx.succeed_unit("seed", len(records))


def test_political_pipeline_into_clickhouse_sinks():
    db = FakeClickHouse(exchanges=US)
    sinks = ClickHouseSinks(V2USStorage(db))
    _seed_listings(sinks)
    session = Session(
        {
            "legislators-current": body("congress_legislators", "ref.legislators", "legislators"),
            "committees-current": body("congress_legislators", "ref.legislators", "committees"),
            "committee-membership": body("congress_legislators", "ref.legislators", "memberships"),
            "2026FD.ZIP": body("house_clerk", "alt.political_filings", "index_2026"),
            "20000001.pdf": body("house_clerk", "alt.political_trades", "ptr_20000001"),
        }
    )

    def clock():
        return NOW

    legislators = Binding(dataset="ref.legislators", market="USA", provider="congress_legislators")
    summary = run_binding(
        legislators,
        CongressLegislators(CongressLegislatorsSettings(), session=session, clock=clock),
        sinks,
        reference_request(legislators),
    )
    assert [u.name for u in summary.units] == [
        "file:legislators",
        "file:committees",
        "file:memberships",
    ]  # dependency order
    assert summary.status == "success"
    aliases = {(a["alias_kind"], a["alias_value"]) for a in db.rows("ref.identifier_aliases")}
    assert {("bioguide", "E000001"), ("legislator_name", "ada|example")} <= aliases
    assert len(db.rows("alt.political_committee_memberships")) == 2  # ghost member parked
    assert any(r["alias_value"] == "G000009" for r in db.log["meta.unresolved_entities"])
    assert {r["role"] for r in db.rows("alt.political_committee_memberships")} == {
        "chair",
        "ranking_member",
    }

    filings = Binding(
        dataset="alt.political_filings",
        market="USA",
        provider="house_clerk",
        params={"years": [2026]},
    )
    run_binding(
        filings,
        HouseClerkFilings(HouseClerkSettings(), session=session, clock=clock),
        sinks,
        ReferenceRequest("USA", params={"years": [2026]}),
    )
    by_id = {f["filing_id"]: f for f in db.rows("alt.political_filings")}
    assert by_id["20000001"]["bioguide_id"] == "E000001"  # "Ada M." matched by name key
    assert by_id["20000002"]["bioguide_confidence"] == "unresolved"

    recent = sinks.recent_filings(chamber="house", limit=1)
    assert [f.filing_id for f in recent] == ["20000002"]  # newest first
    trades = Binding(dataset="alt.political_trades", market="USA", provider="house_clerk")
    target = [
        f for f in sinks.recent_filings(chamber="house", limit=5) if f.filing_id == "20000001"
    ]
    summary = run_binding(
        trades,
        HouseClerkTrades(HouseClerkSettings(), session=session, clock=clock),
        sinks,
        FilingsRequest("USA", tuple(target)),
    )
    assert (summary.status, summary.rows_written) == ("success", 4)
    rows = {r["ticker_raw"]: r for r in db.rows("alt.political_trades")}
    assert rows["BRK.B"]["listing_id"] is not None  # canonicalised to BRK-B and resolved
    assert rows["AAPL"]["resolution_confidence"] == "exact"
    assert rows["BILL"]["listing_id"] is None
    assert {r["bioguide_id"] for r in rows.values()} == {"E000001"}
    assert by_id["20000001"]["trade_count"] == 0
    assert {f["filing_id"]: f["trade_count"] for f in db.rows("alt.political_filings")}[
        "20000001"
    ] == 4

    # Re-syncing the index keeps the trade count (the legacy writer reset it to 0).
    run_binding(
        filings,
        HouseClerkFilings(HouseClerkSettings(), session=session, clock=clock),
        sinks,
        ReferenceRequest("USA", params={"years": [2026]}),
    )
    assert {f["filing_id"]: f["trade_count"] for f in db.rows("alt.political_filings")}[
        "20000001"
    ] == 4


def test_house_clerk_refuses_foreign_urls_and_shadow_runs_do_not_write():
    sink = InMemorySink()
    source = HouseClerkTrades(HouseClerkSettings(), session=Session({}), clock=lambda: NOW)
    bad = FilingsRequest("USA", (kit.FilingRef("1", 2026, "https://evil.example/1.pdf"),))
    binding = Binding(dataset="alt.political_trades", market="USA", provider="house_clerk")
    summary = run_binding(binding, source, sink, bad)
    assert summary.status == "failed" and "non-House-Clerk" in summary.failed_units[0].error

    session = Session({"2026FD.ZIP": body("house_clerk", "alt.political_filings", "index_2026")})
    shadow = Binding(
        dataset="alt.political_filings", market="USA", provider="house_clerk", role="shadow"
    )
    result = run_binding(
        shadow,
        HouseClerkFilings(HouseClerkSettings(), session=session, clock=lambda: NOW),
        sink,
        ReferenceRequest("USA", params={"years": [2026]}),
    )
    assert (result.source, result.rows_written, sink.filings) == ("house_clerk:shadow", 0, {})


def test_memory_sink_political_semantics_match():
    sink = InMemorySink()
    source = CongressLegislators(
        CongressLegislatorsSettings(),
        session=Session(
            {
                "legislators-current": body(
                    "congress_legislators", "ref.legislators", "legislators"
                ),
                "committees-current": body("congress_legislators", "ref.legislators", "committees"),
                "committee-membership": body(
                    "congress_legislators", "ref.legislators", "memberships"
                ),
            }
        ),
        clock=lambda: NOW,
    )
    binding = Binding(dataset="ref.legislators", market="USA", provider="congress_legislators")
    summary = run_binding(binding, source, sink, reference_request(binding))
    assert summary.rows_written == 2 + 2 + 2  # legislators, committees, known memberships
    assert date(2026, 9, 24) == NOW.date()


def _bootstrap_sources(monkeypatch, session):
    from factorlab.components.ingest_political import bootstrap

    def build(binding):
        if binding.dataset == "ref.legislators":
            return CongressLegislators(
                CongressLegislatorsSettings(), session=session, clock=lambda: NOW
            )
        cls = HouseClerkFilings if binding.dataset == "alt.political_filings" else HouseClerkTrades
        return cls(HouseClerkSettings(), session=session, clock=lambda: NOW)

    monkeypatch.setattr(bootstrap, "source_for", build)
    return bootstrap


def _bootstrap_routes():
    return {
        "legislators-current": body("congress_legislators", "ref.legislators", "legislators"),
        "committees-current": body("congress_legislators", "ref.legislators", "committees"),
        "committee-membership": body("congress_legislators", "ref.legislators", "memberships"),
        "2026FD.ZIP": body("house_clerk", "alt.political_filings", "index_2026"),
        "20000001.pdf": body("house_clerk", "alt.political_trades", "ptr_20000001"),
        "20000002.pdf": body("house_clerk", "alt.political_trades", "ptr_20000001"),
    }


def test_bootstrap_retains_one_run_and_each_dataset_lineage(monkeypatch):
    db = FakeClickHouse(exchanges=US)
    sink = ClickHouseSinks(V2USStorage(db))
    bootstrap = _bootstrap_sources(monkeypatch, Session(_bootstrap_routes()))
    monkeypatch.setattr(ClickHouseSinks, "from_environment", lambda market: sink)
    monkeypatch.setattr(sink, "close", lambda: None)
    assert bootstrap.main(["--year", "2026", "--congress", "118", "--ptr-limit", "2"]) == 0
    runs = db.rows("meta.ingestion_runs")
    assert len({r["run_id"] for r in runs}) == 1
    run = runs[-1]
    assert (run["pipeline"], run["status"], run["requested_series"], run["successful_series"]) == (
        "political_bootstrap",
        "success",
        6,
        6,
    )
    archives = {r["raw_id"]: r for r in db.rows("raw.archive")}
    assert len(archives) == 6
    for table in (
        "alt.political_filings",
        "alt.political_trades",
        "alt.political_committees",
        "alt.political_committee_memberships",
    ):
        assert db.rows(table)
        for row in db.rows(table):
            raw = archives[row["raw_id"]]
            assert row["ingest_run_id"] == run["run_id"]
            assert row["source"] == raw["source"]
            assert row["as_of_time"] == NOW
    assert {r["congress_number"] for r in db.rows("alt.political_committees")} == {118}
    assert {r["congress_number"] for r in db.rows("alt.political_committee_memberships")} == {118}


def test_bootstrap_defers_filings_after_reference_failure(monkeypatch):
    from factorlab.ingest.bindings import load_bindings

    routes = _bootstrap_routes()
    routes["legislators-current"] = b"invalid JSON"
    session = Session(routes)
    bootstrap = _bootstrap_sources(monkeypatch, session)
    sink = ClickHouseSinks(V2USStorage(FakeClickHouse(exchanges=US)))
    bindings = {b.dataset: b for b in load_bindings().bindings if b.dataset in bootstrap.DATASETS}
    with ingestion_run(
        sink, pipeline="political_bootstrap", source="political", market_code="ALT_POLITICAL"
    ) as ctx:
        bootstrap.collect(ctx, sink, bindings, year=2026, congress=119, ptr_limit=2)
    assert ctx.status == "partial"
    assert not any("2026FD.ZIP" in url for url in session.calls)
    assert {u.name for u in ctx.units if not u.ok} >= {
        "alt.political_filings",
        "alt.political_trades",
    }


def test_recent_filings_filters_year_before_limiting():
    db = FakeClickHouse(exchanges=US)
    records = [
        {
            "country_code": "US",
            "chamber": "house",
            "filing_id": "old",
            "filing_year": 2025,
            "filing_date": date(2025, 5, 1),
            "filing_url": "https://disclosures-clerk.house.gov/old.pdf",
            "version": 1,
        },
        {
            "country_code": "US",
            "chamber": "house",
            "filing_id": "new",
            "filing_year": 2026,
            "filing_date": date(2026, 5, 1),
            "filing_url": "https://disclosures-clerk.house.gov/new.pdf",
            "version": 1,
        },
    ]
    columns = list(records[0])
    db.insert(
        "alt.political_filings", [[r[c] for c in columns] for r in records], column_names=columns
    )
    sink = ClickHouseSinks(V2USStorage(db))
    assert [
        f.filing_id for f in sink.recent_filings(chamber="house", limit=1, filing_year=2025)
    ] == ["old"]


def _trade(ticker, day):
    from uuid import uuid4

    from factorlab.storage.sinks.political import TRADE_COLUMNS

    trade = dict.fromkeys(TRADE_COLUMNS)
    return {
        **trade,
        "political_trade_id": uuid4(),
        "country_code": "US",
        "chamber": "house",
        "filing_id": "20000001",
        "transaction_date": day,
        "ticker_raw": ticker,
        "resolution_confidence": "unresolved",
        "version": 1,
    }


def test_duplicate_listings_retire_and_political_trades_reresolve():
    from uuid import UUID

    from factorlab.storage.identity_repair import retire_duplicate_listings

    db = FakeClickHouse(exchanges=US)
    sinks = ClickHouseSinks(V2USStorage(db))
    # first_traded is when FactorLab first saw each listing: after every trade below.
    records = [
        InstrumentRecord(
            ref=InstrumentRef("eodhd_symbol", f"{alias}.US", ex, symbol, "US"),
            name=symbol,
            product_type="common",
            currency="USD",
            first_traded=date(2026, 9, 24),
        )
        for alias, symbol, ex in (
            ("AAPL", "AAPL", "XNAS"),
            ("AMZN", "AMZN", "XNAS"),
            ("BRK-B", "BRK.B", "XNYS"),
        )
    ]
    with ingestion_run(sinks, pipeline="seed", source="eodhd", market_code="USA") as ctx:
        sinks.upsert_instruments(
            records,
            provenance=ctx.provenance(source_channel="eodhd:seed", raw_id=None, as_of_time=NOW),
        )
        ctx.succeed_unit("seed", len(records))
    [canonical] = [r for r in db.rows("ref.listings") if r["trading_symbol"] == "AAPL"]
    # A cutover-era duplicate: same exchange and symbol, another security, a legacy alias.
    orphan, security = UUID(int=1), UUID(int=2)
    db.insert(
        "ref.securities",
        [[security, security, "common", None]],
        column_names=["security_id", "entity_id", "security_type", "isin"],
    )
    duplicate = {**canonical, "listing_id": orphan, "security_id": security}
    db.insert("ref.listings", [list(duplicate.values())], column_names=list(duplicate))
    [alias] = [a for a in db.rows("ref.identifier_aliases") if a["alias_value"] == "AAPL.US"]
    legacy = {
        **alias,
        "alias_kind": "upstox_instrument_key",
        "alias_value": "schwab:USA:AAPL",
        "target_id": orphan,
        "valid_from": date(2026, 9, 18),
    }
    db.insert("ref.identifier_aliases", [list(legacy.values())], column_names=list(legacy))
    day = date(2026, 7, 10)
    trades = [_trade(t, day) for t in ("AAPL", "AMZN", "BRK.B")]
    db.insert(
        "alt.political_trades", [list(t.values()) for t in trades], column_names=list(trades[0])
    )

    # Filings spell class shares BRK-B; trades predate first_traded.
    assert sinks._listing_on(InstrumentRef("", "", "", "BRK-B", "US"), day) is not None
    dry = sinks.reresolve_political_trades()
    assert (dry["applied"], dry["candidates"], dry["relinked"]) == (False, 3, 2)
    assert dry["unresolved_tickers"] == {"AAPL": 1}  # two AAPL listings: never guess
    assert all(t["listing_id"] is None for t in db.rows("alt.political_trades"))

    plan = retire_duplicate_listings(db, country="US", canonical_alias_kind="eodhd_symbol")
    assert [(r.symbol, r.orphan, r.canonical) for r in plan.retirements] == [
        ("AAPL", orphan, canonical["listing_id"])
    ]
    assert (plan.aliases_moved, plan.applied) == (1, False)
    assert not db.rows("ref.listing_migrations")
    done = retire_duplicate_listings(
        db, country="US", canonical_alias_kind="eodhd_symbol", apply=True, now=NOW
    )
    assert done.applied and len(db.rows("ref.listing_migrations")) == 1
    assert {r["listing_id"]: r["active"] for r in db.rows("ref.listings")}[orphan] is False
    moved = {
        (a["target_id"], a["valid_to"])
        for a in db.rows("ref.identifier_aliases")
        if a["alias_value"] == "schwab:USA:AAPL"
    }
    assert moved == {(orphan, NOW.date()), (canonical["listing_id"], None)}

    applied = sinks.reresolve_political_trades(apply=True)
    assert (applied["relinked"], applied["still_unresolved"]) == (3, 0)
    rows = {t["ticker_raw"]: t for t in db.rows("alt.political_trades")}
    assert rows["AAPL"]["listing_id"] == canonical["listing_id"]
    assert {t["resolution_confidence"] for t in rows.values()} == {"exact"}
    # Both steps are idempotent.
    again = retire_duplicate_listings(
        db, country="US", canonical_alias_kind="eodhd_symbol", apply=True, now=NOW
    )
    assert again.retirements == []
    assert sinks.reresolve_political_trades()["candidates"] == 0
