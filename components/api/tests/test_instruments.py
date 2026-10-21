from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factorlab.components.api.catalog_query import CatalogError, CatalogTooExpensive
from factorlab.components.api.instruments import (
    Instrument,
    InstrumentRepository,
    Kind,
    current_sector_sql,
    inventory_sql,
    registry,
    repository,
    router,
    scope,
)
from factorlab.components.api.schema_map import _planned_v2_tables

ID = UUID("00000000-0000-0000-0000-000000000001")


def instrument(kind: Kind = "listing"):
    return Instrument(
        kind=kind,
        id=ID,
        listing_id=ID,
        security_id=ID,
        entity_id=ID,
        symbol="SAME",
        name="Example",
        country="US",
        exchange="NYSE",
        instrument_type="common",
        active=False,
        configured=False,
    )


def repo_with(*responses):
    repo = InstrumentRepository(Mock())
    repo.instrument = Mock(return_value=instrument())
    repo.query = Mock(side_effect=responses)
    return repo


def test_registry_covers_every_schema_table_and_view_and_reviewed_columns():
    tables = {t.name: t for t in _planned_v2_tables()}
    assert set(registry()) == set(tables), "Classify new datasets explicitly"
    for name, entry in registry().items():
        assert entry["columns"] == [c.name for c in tables[name].columns]
        assert entry["engine"] == tables[name].engine
        assert entry["classification"] in {
            "queryable",
            "related_context",
            "unsupported",
            "investigation",
        }


def test_inventory_keeps_inactive_empty_and_duplicate_symbols_and_parameterizes_search():
    one = instrument().model_dump()
    two = {**one, "id": UUID(int=2), "exchange": "NASDAQ"}
    repo = repo_with([{"total": 2}], [one, two])
    result = repo.inventory("x' OR 1=1", "", "", "", "all", "all", 50, 0)
    assert result.total == 2
    assert [i.exchange for i in result.items] == ["NYSE", "NASDAQ"]
    assert isinstance(repo.query, Mock)
    sql, params = repo.query.call_args.args
    assert "x' OR" not in sql
    assert params["search"] == "x' OR 1=1"
    assert "ref.identifier_aliases FINAL" in sql
    assert "market.bars" not in inventory_sql()
    assert "UNION ALL" in inventory_sql()


@pytest.mark.parametrize(
    "table",
    [
        "broker.positions_snapshot",
        "broker.executions",
        "market.options_bars",
        "market.futures_contract_bars",
        "alt.political_trades",
        "meta.expected_series",
    ],
)
def test_contract_scope_cannot_match_underlying_or_sibling(table):
    predicate, _ = scope(registry()[table], "contract")
    assert predicate == "(contract_id = {id:UUID})"


def test_relationships_are_disjoint_and_filings_use_composite_identity():
    predicate, labels = scope(registry()["alt.political_trades"], "listing")
    assert "contract_id IS NULL AND listing_id IS NULL AND security_id IS NULL" in predicate
    assert "'issuer'" in labels and "'security'" in labels
    filing, _ = scope(registry()["alt.political_filings"], "contract")
    assert "tuple(country_code, chamber, filing_id)" in filing
    assert "contract_id = {id:UUID}" in filing
    social, _ = scope(registry()["alt.social_reddit_posts"], "listing")
    assert "NOT has(mentioned_listings" in social
    assert "has(mentioned_entities" in social
    assert scope(registry()["market.bars"], "contract")[0] == "0"


def test_unresolved_political_trades_link_by_filing_ticker_last():
    rules = registry()["alt.political_trades"]["relationships"]["listing"]
    label, rule = rules[-1]
    assert label == "filing ticker (unresolved)"
    # Only rows the writer could not resolve, scoped to the instrument's country.
    assert "listing_id IS NULL AND security_id IS NULL AND entity_id IS NULL" in rule
    assert "ticker_raw = {symbol:String} AND country_code = {country:String}" in rule
    filing, _ = scope(registry()["alt.political_filings"], "listing")
    assert "ticker_raw = {symbol:String}" in filing


@pytest.mark.parametrize(
    "table", ["alt.political_committee_memberships", "alt.political_committees"]
)
def test_committees_are_related_context_through_legislators_who_traded(table):
    entry = registry()[table]
    assert entry["classification"] == "related_context"
    listing, _ = scope(entry, "listing")
    assert "FROM alt.political_trades FINAL WHERE legislator_entity_id IS NOT NULL" in listing
    assert "listing_id = {listing_id:UUID}" in listing
    contract, _ = scope(entry, "contract")
    assert "contract_id = {id:UUID}" in contract


def test_broker_links_are_scoped_and_ambiguous_execution_ids_excluded():
    orders, _ = scope(registry()["book.order_assignment_events"], "contract")
    assert "tuple(broker_code, account_id, perm_id)" in orders
    assert "contract_id = {id:UUID}" in orders
    attribution, _ = scope(registry()["book.attribution"], "listing")
    assert "uniqExact(tuple(broker_code, account_id)) = 1" in attribution
    assert scope(registry()["broker.account_state_snapshot"], "listing")[0] == "0"


def test_candidates_require_market_and_alias_validity_and_are_not_confirmed():
    candidate = registry()["meta.unresolved_entities"]
    assert candidate["classification"] == "investigation"
    predicate, _ = scope(candidate, "listing")
    for text in [
        "scope_country",
        "scope_exchange",
        "valid_from",
        "valid_to",
        "resolved_at IS NULL",
    ]:
        assert text in predicate


def test_lifetime_coverage_is_scoped_final_and_separates_providers():
    repo = repo_with(
        [{"name": "bars"}],
        [
            {"relationship": "direct", "source": "a", "rows": 1},
            {"relationship": "direct", "source": "b", "rows": 2},
        ],
    )
    result = repo.dataset("listing", ID, "market.bars")
    assert result.state == "stored" and len(result.groups) == 2
    assert isinstance(repo.query, Mock)
    sql, params, _ = repo.query.call_args.args
    assert "market.bars FINAL" in sql
    assert "WHERE (" in sql and "listing_id = {id:UUID}" in sql
    assert "GROUP BY relationship, `source`, `resolution`" in sql
    assert "{start:" not in sql
    assert params["id"] == ID


@pytest.mark.parametrize(
    "response,state", [([], "missing_table"), (CatalogTooExpensive("Limit reached"), "unavailable")]
)
def test_missing_table_and_limits_are_not_zero_coverage(response, state):
    repo = repo_with(response)
    result = repo.dataset("listing", ID, "market.bars")
    assert result.state == state and result.groups == []


def test_empty_and_unsupported_distinct():
    assert repo_with([{"name": "bars"}], []).dataset("listing", ID, "market.bars").state == "empty"
    assert repo_with().dataset("contract", ID, "market.bars").state == "unsupported"


def test_preview_dates_provider_pagination_final_and_sanitization():
    repo = repo_with([{"name": "bars"}], [{"source": "a", "raw_id": None}, {"source": "b"}])
    result = repo.dataset("listing", ID, "market.bars", preview=True, provider="a'", limit=1)
    assert result.has_more and len(result.rows) == 1
    assert isinstance(repo.query, Mock)
    sql, params, _ = repo.query.call_args.args
    assert "a'" not in sql and params["provider"] == "a'"
    assert result.end is not None and result.start is not None
    assert (result.end - result.start).days == 30
    assert "LIMIT {limit:UInt16} OFFSET {offset:UInt32}" in sql
    assert result.evidence["raw_id"]["state"] == "unavailable"


def test_provenance_uses_recorded_ids_and_raw_metadata_allowlist():
    repo = repo_with([{"raw_id": ID, "source_url": "https://example.test/?token=secret"}], [])
    result = repo.provenance([{"raw_id": ID, "ingest_run_id": ID}])
    assert isinstance(repo.query, Mock)
    sql, params, _ = repo.query.call_args_list[0].args
    assert params["ids"] == [str(ID)]
    assert "response_body" not in sql and "response_headers" not in sql
    assert "cutQueryStringAndFragment" in sql
    assert "secret" not in str(result)
    assert result["ingest_run_id"]["unavailable_ids"] == [str(ID)]
    assert "metadata_json" not in repo.query.call_args_list[1].args[0]


def test_preview_policy_applies_to_coverage_and_rows(monkeypatch):
    monkeypatch.setenv("FACTORLAB_CATALOG_PREVIEW", "off")
    for preview in (False, True):
        result = repo_with([{"name": "bars"}]).dataset(
            "listing", ID, "market.bars", preview=preview
        )
        assert result.state == "unavailable"


def test_resource_limits_throw_instead_of_returning_partial_counts():
    client = Mock()
    client.query.return_value.column_names = ["rows"]
    client.query.return_value.result_rows = [(1,)]
    InstrumentRepository(client).query("SELECT 1 AS rows", {})
    settings = client.query.call_args.kwargs["settings"]
    assert settings["readonly"] == 2
    assert settings["result_overflow_mode"] == "throw"
    assert settings["read_overflow_mode"] == "throw"


def test_routes_validate_identifiers_limits_and_dates_without_database():
    app = FastAPI()
    app.include_router(router)
    repo = Mock()
    app.dependency_overrides[repository] = lambda: repo
    client = TestClient(app)
    for path in [
        "/instruments/listing/not-uuid",
        f"/instruments/equity/{ID}",
        "/instruments?limit=201",
        f"/instruments/listing/{ID}/datasets/market.bars/rows?offset=5001",
    ]:
        assert client.get(path).status_code == 422
    repo.identity.side_effect = CatalogError("Unavailable")
    assert client.get(f"/instruments/listing/{ID}").json()["detail"] == "Unavailable"
    bad = repo_with([{"name": "bars"}])
    with pytest.raises(Exception, match="increasing date window"):
        bad.dataset(
            "listing",
            ID,
            "market.bars",
            preview=True,
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_inventory_carries_current_gics_sector_and_filters_by_sector_and_kind():
    one = {**instrument().model_dump(), "sector_id": "45", "sector": "Information Technology"}
    two = {**instrument().model_dump(), "id": UUID(int=2), "sector_id": "", "sector": ""}
    repo = repo_with([{"total": 2}], [one, two])
    result = repo.inventory("", "US", "", "", "all", "all", 50, 0, sector="none", kind="listing")
    assert [i.sector for i in result.items] == ["Information Technology", ""]
    assert isinstance(repo.query, Mock)
    sql, params = repo.query.call_args.args
    assert params["sector"] == "none" and params["kind"] == "listing"
    assert "ref.security_sector_history AS h FINAL" in sql
    assert "if({sector:String} = 'none', '', {sector:String})" in sql
    assert "kind = {kind:String}" in sql
    # Only the open-ended (current) GICS row counts; a closed row is history.
    assert "effective_to IS NULL" in current_sector_sql()
    assert "classification = 'gics'" in current_sector_sql()


def test_sector_summary_counts_listings_and_separates_unclassified():
    repo = repo_with(
        [
            {"sector_id": "", "sector": "", "count": 3},
            {"sector_id": "10", "sector": "Energy", "count": 4},
            {"sector_id": "45", "sector": "Information Technology", "count": 5},
        ]
    )
    result = repo.sectors("app", "US", "yes", "all")
    assert [(i.sector_id, i.count) for i in result.items] == [("10", 4), ("45", 5)]
    assert result.unclassified == 3 and result.total == 12
    assert isinstance(repo.query, Mock)
    sql, params = repo.query.call_args.args
    assert params["kind"] == "listing" and params["search"] == "app"
    assert params["country"] == "US" and params["active"] == "yes"
    assert "GROUP BY sector_id" in sql


def test_sector_routes_validate_and_pass_filters():
    app = FastAPI()
    app.include_router(router)
    repo = Mock()
    app.dependency_overrides[repository] = lambda: repo
    client = TestClient(app)
    assert client.get("/instruments?sector=x'--").status_code == 422
    assert client.get("/instruments?kind=equity").status_code == 422
    repo.inventory.return_value = {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert client.get("/instruments?sector=45&kind=listing").status_code == 200
    assert repo.inventory.call_args.kwargs == {"sector": "45", "kind": "listing"}
    repo.sectors.return_value = {"items": [], "unclassified": 0, "total": 0}
    assert client.get("/sectors?search=%20ai%20").status_code == 200
    repo.sectors.assert_called_with("ai", "US", "all", "all")
