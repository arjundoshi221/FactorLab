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
from tests.contracts import kit
from tests.contracts.fake_clickhouse import FakeClickHouse

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
        "chair", "vice_chair", "ranking_member", "member"]
    assert canonical_ticker("BRK.B") == "BRK-B" and canonical_ticker("not a ticker!") is None


def _seed_listings(sinks):
    records = [InstrumentRecord(ref=InstrumentRef("eodhd_symbol", f"{s}.US", ex, s, "US"),
                                name=s, product_type="common", currency="USD",
                                first_traded=date(1996, 1, 1))
               for s, ex in (("AAPL", "XNAS"), ("AMZN", "XNAS"), ("BRK-B", "XNYS"))]
    with ingestion_run(sinks, pipeline="seed", source="eodhd", market_code="USA") as ctx:
        sinks.upsert_instruments(records, provenance=ctx.provenance(
            source_channel="eodhd:seed", raw_id=None, as_of_time=NOW))
        ctx.succeed_unit("seed", len(records))


def test_political_pipeline_into_clickhouse_sinks():
    db = FakeClickHouse(exchanges=US)
    sinks = ClickHouseSinks(V2USStorage(db))
    _seed_listings(sinks)
    session = Session({
        "legislators-current": body("congress_legislators", "ref.legislators", "legislators"),
        "committees-current": body("congress_legislators", "ref.legislators", "committees"),
        "committee-membership": body("congress_legislators", "ref.legislators", "memberships"),
        "2026FD.ZIP": body("house_clerk", "alt.political_filings", "index_2026"),
        "20000001.pdf": body("house_clerk", "alt.political_trades", "ptr_20000001"),
    })
    clock = lambda: NOW

    legislators = Binding(dataset="ref.legislators", market="USA",
                          provider="congress_legislators")
    summary = run_binding(legislators, CongressLegislators(
        CongressLegislatorsSettings(), session=session, clock=clock), sinks,
        reference_request(legislators))
    assert [u.name for u in summary.units] == [
        "file:legislators", "file:committees", "file:memberships"]  # dependency order
    assert summary.status == "success"
    aliases = {(a["alias_kind"], a["alias_value"]) for a in db.rows("ref.identifier_aliases")}
    assert {("bioguide", "E000001"), ("legislator_name", "ada|example")} <= aliases
    assert len(db.rows("alt.political_committee_memberships")) == 2  # ghost member parked
    assert any(r["alias_value"] == "G000009" for r in db.log["meta.unresolved_entities"])
    assert {r["role"] for r in db.rows("alt.political_committee_memberships")} == {
        "chair", "ranking_member"}

    filings = Binding(dataset="alt.political_filings", market="USA", provider="house_clerk",
                      params={"years": [2026]})
    run_binding(filings, HouseClerkFilings(HouseClerkSettings(), session=session, clock=clock),
                sinks, ReferenceRequest("USA", params={"years": [2026]}))
    by_id = {f["filing_id"]: f for f in db.rows("alt.political_filings")}
    assert by_id["20000001"]["bioguide_id"] == "E000001"  # "Ada M." matched by name key
    assert by_id["20000002"]["bioguide_confidence"] == "unresolved"

    recent = sinks.recent_filings(chamber="house", limit=1)
    assert [f.filing_id for f in recent] == ["20000002"]  # newest first
    trades = Binding(dataset="alt.political_trades", market="USA", provider="house_clerk")
    target = [f for f in sinks.recent_filings(chamber="house", limit=5)
              if f.filing_id == "20000001"]
    summary = run_binding(trades, HouseClerkTrades(HouseClerkSettings(), session=session,
                                                   clock=clock), sinks,
                          FilingsRequest("USA", tuple(target)))
    assert (summary.status, summary.rows_written) == ("success", 4)
    rows = {r["ticker_raw"]: r for r in db.rows("alt.political_trades")}
    assert rows["BRK.B"]["listing_id"] is not None  # canonicalised to BRK-B and resolved
    assert rows["AAPL"]["resolution_confidence"] == "exact"
    assert rows["BILL"]["listing_id"] is None
    assert {r["bioguide_id"] for r in rows.values()} == {"E000001"}
    assert by_id["20000001"]["trade_count"] == 0
    assert {f["filing_id"]: f["trade_count"] for f in db.rows("alt.political_filings")}[
        "20000001"] == 4

    # Re-syncing the index keeps the trade count (the legacy writer reset it to 0).
    run_binding(filings, HouseClerkFilings(HouseClerkSettings(), session=session, clock=clock),
                sinks, ReferenceRequest("USA", params={"years": [2026]}))
    assert {f["filing_id"]: f["trade_count"] for f in db.rows("alt.political_filings")}[
        "20000001"] == 4


def test_house_clerk_refuses_foreign_urls_and_shadow_runs_do_not_write():
    sink = InMemorySink()
    source = HouseClerkTrades(HouseClerkSettings(), session=Session({}), clock=lambda: NOW)
    bad = FilingsRequest("USA", (kit.FilingRef("1", 2026, "https://evil.example/1.pdf"),))
    binding = Binding(dataset="alt.political_trades", market="USA", provider="house_clerk")
    summary = run_binding(binding, source, sink, bad)
    assert summary.status == "failed" and "non-House-Clerk" in summary.failed_units[0].error

    session = Session({"2026FD.ZIP": body("house_clerk", "alt.political_filings", "index_2026")})
    shadow = Binding(dataset="alt.political_filings", market="USA", provider="house_clerk",
                     role="shadow")
    result = run_binding(shadow, HouseClerkFilings(HouseClerkSettings(), session=session,
                                                   clock=lambda: NOW), sink,
                         ReferenceRequest("USA", params={"years": [2026]}))
    assert (result.source, result.rows_written, sink.filings) == ("house_clerk:shadow", 0, {})


def test_memory_sink_political_semantics_match():
    sink = InMemorySink()
    source = CongressLegislators(CongressLegislatorsSettings(), session=Session({
        "legislators-current": body("congress_legislators", "ref.legislators", "legislators"),
        "committees-current": body("congress_legislators", "ref.legislators", "committees"),
        "committee-membership": body("congress_legislators", "ref.legislators", "memberships"),
    }), clock=lambda: NOW)
    binding = Binding(dataset="ref.legislators", market="USA", provider="congress_legislators")
    summary = run_binding(binding, source, sink, reference_request(binding))
    assert summary.rows_written == 2 + 2 + 2  # legislators, committees, known memberships
    assert date(2026, 9, 24) == NOW.date()
