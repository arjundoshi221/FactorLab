"""P8 proof: EDGAR fundamentals as a new provider + dataset through the engine."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from factorlab.ingest.bindings import Binding
from factorlab.ingest.datasets import InstrumentRecord, InstrumentRef
from factorlab.ingest.datasets.fundamentals import CompanyRef, CompanyRequest
from factorlab.ingest.engine import run_binding
from factorlab.ingest.errors import AuthRequired
from factorlab.ingest.provider import ingestion_run
from factorlab.sources.edgar.fundamentals_source import EdgarCompanyFacts, EdgarSettings
from factorlab.storage.canonical_ids import filing_id
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.sinks.fundamentals import unit_class
from factorlab.storage.v2_us import V2USStorage
from factorlab.testkit import conformance as kit
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
BINDING = Binding(dataset="fundamentals.filings", market="USA", provider="edgar")


class Response:
    def __init__(self, body, status=200):
        self.content, self.status_code = body, status
        self.headers = {"Content-Type": "application/json", "Set-Cookie": "x"}


class Session:
    def __init__(self, body):
        self.body = body
        self.agents = []

    def get(self, url, headers=None, timeout=None):
        self.agents.append((headers or {}).get("User-Agent"))
        return Response(self.body if "CIK0000320193" in url else b"{}", 200)


def _source(session, agent="FactorLab research@example.com"):
    return EdgarCompanyFacts(
        EdgarSettings(), session=session, clock=lambda: NOW, secret=lambda name, default="": agent
    )


def _facts():
    return {n: c for n, c, _ in kit.cases("edgar", "fundamentals.filings")}[
        "aapl_companyfacts"
    ].body


def test_unit_mapping():
    assert unit_class("USD") == ("currency", "USD")
    assert unit_class("USD/shares") == ("usd_per_share", "USD")
    assert unit_class("shares") == ("shares", None)
    assert unit_class("pure") == ("ratio", None)


def test_missing_user_agent_is_auth_required():
    with pytest.raises(AuthRequired):
        _source(Session(b"{}"), agent="").fetch(
            _source(Session(b"{}")).plan(CompanyRequest("USA", (CompanyRef("320193"),)))[0]
        )


def test_fundamentals_learn_the_cik_alias_then_resolve_by_it():
    db = FakeClickHouse(exchanges={"XNAS": ("XNAS", "US", "USD")})
    sinks = ClickHouseSinks(V2USStorage(db))
    with ingestion_run(sinks, pipeline="seed", source="eodhd", market_code="USA") as ctx:
        sinks.upsert_instruments(
            [
                InstrumentRecord(
                    ref=InstrumentRef(
                        "eodhd_symbol", "AAPL.US", "XNAS", "AAPL", "US", isin="US0378331005"
                    ),
                    name="Apple Inc.",
                    product_type="common",
                    currency="USD",
                )
            ],
            provenance=ctx.provenance(source_channel="eodhd:seed", raw_id=None, as_of_time=NOW),
        )
        ctx.succeed_unit("seed", 1)
    session = Session(_facts())
    request = CompanyRequest("USA", (CompanyRef("320193", "AAPL"),))
    summary = run_binding(BINDING, _source(session), sinks, request)
    assert (summary.status, summary.rows_written) == ("success", 8)
    assert session.agents == ["FactorLab research@example.com"]

    [entity] = [e["entity_id"] for e in db.rows("ref.entities")]
    cik_alias = [a for a in db.rows("ref.identifier_aliases") if a["alias_kind"] == "cik"]
    assert [(a["alias_value"], a["target_id"], a["confidence"]) for a in cik_alias] == [
        ("0000320193", entity, "medium")
    ]

    filings = {f["accession_number"]: f for f in db.rows("fundamentals.filings")}
    ten_q = filings["0000320193-26-000071"]
    assert ten_q["filing_id"] == filing_id("sec", "0000320193-26-000071")
    assert (ten_q["period_type"], ten_q["fiscal_period"], ten_q["period_end"]) == (
        "quarterly",
        "Q3",
        date(2026, 6, 27),
    )
    assert filings["0000320193-26-000080"]["is_amendment"] is True
    eps = next(
        i
        for i in db.rows("fundamentals.line_items")
        if i["tag"] == "us-gaap:EarningsPerShareDiluted"
    )
    assert (eps["unit"], eps["currency_code"], eps["period_type"]) == (
        "usd_per_share",
        "USD",
        "duration",
    )
    assets = next(i for i in db.rows("fundamentals.line_items") if i["tag"] == "us-gaap:Assets")
    assert (assets["period_type"], assets["context_ref"]) == ("point", "CY2026Q2I")
    assert all(i["entity_id"] == entity for i in db.rows("fundamentals.line_items"))

    # Second run without a ticker hint resolves through the learned alias.
    again = run_binding(
        BINDING, _source(Session(_facts())), sinks, CompanyRequest("USA", (CompanyRef("320193"),))
    )
    assert (again.status, again.rows_written) == ("success", 8)


def test_unknown_issuer_is_parked():
    db = FakeClickHouse()
    sinks = ClickHouseSinks(V2USStorage(db))
    summary = run_binding(
        BINDING, _source(Session(_facts())), sinks, CompanyRequest("USA", (CompanyRef("320193"),))
    )
    assert summary.rows_written == 0
    assert db.rows("fundamentals.filings") == []
    assert any(r["alias_value"] == "0000320193" for r in db.log["meta.unresolved_entities"])
