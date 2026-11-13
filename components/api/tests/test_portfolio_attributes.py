from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.components.api.portfolio_attributes import (
    MULTI_SECTOR_ETF,
    UNCLASSIFIED,
    enrich,
    parse_dims,
)
from factorlab.components.api.portfolio_tagging import with_attributes

AAPL = "11111111-1111-1111-1111-111111111111"
XLK = "22222222-2222-2222-2222-222222222222"
IWV = "33333333-3333-3333-3333-333333333333"
FUT = "44444444-4444-4444-4444-444444444444"
NVDA = "55555555-5555-5555-5555-555555555555"


class Reader:
    """Answers the resolver's reference queries from small in-memory tables."""

    def __init__(self, fail_sectors: bool = False) -> None:
        self.fail_sectors = fail_sectors
        self.queries: list[str] = []

    def query(self, sql, parameters=None):
        self.queries.append(sql)
        parameters = parameters or {}
        if "ref.identifier_aliases" in sql:
            table = {"265598": ("listing", NVDA)}
            return [
                {"alias_value": v, "target_kind": table[v][0], "target_id": table[v][1]}
                for v in parameters["values"]
                if v in table
            ]
        if "ref.contracts" in sql:
            return [{"contract_id": FUT, "underlying_listing_id": AAPL}]
        if "ref.listings" in sql:
            table = {
                AAPL: ("AAPL", "common"),
                XLK: ("XLK", "etf"),
                IWV: ("IWV", "etf"),
                NVDA: ("NVDA", "common"),
            }
            return [
                {"listing_id": i, "symbol": table[i][0], "security_type": table[i][1]}
                for i in parameters["ids"]
                if i in table
            ]
        if "security_sector_history" in sql:
            if self.fail_sectors:
                raise RuntimeError("clickhouse down")
            table = {AAPL: ("45", "Information Technology"), NVDA: ("45", "Information Technology")}
            return [
                {"listing_id": i, "sector_id": table[i][0], "sector": table[i][1]}
                for i in parameters["ids"]
                if i in table
            ]
        raise AssertionError(sql)


def sectors(rows, reader=None):
    labelled, failed = enrich(reader or Reader(), rows, ["sector"])
    assert failed == []
    return [row["attributes"]["sector"] for row in labelled]


def test_sector_resolution_order():
    tech = {"id": "45", "label": "Information Technology"}
    assert sectors(
        [
            {"listing_id": AAPL, "trading_symbol": "AAPL"},
            # A derivative takes its underlying's sector.
            {"contract_id": FUT, "trading_symbol": "AAPL 261218C"},
            # Sector ETFs map through the index catalogue; other ETFs share one bucket.
            {"listing_id": XLK, "trading_symbol": "XLK"},
            {"listing_id": IWV, "trading_symbol": "IWV"},
            # An unresolved broker row is Unclassified, never guessed from its ticker.
            {
                "listing_id": None,
                "vendor_id": "999",
                "broker_code": "ibkr",
                "trading_symbol": "ZZZ",
            },
            {"listing_id": "00000000-0000-0000-0000-000000000000", "trading_symbol": ""},
        ]
    ) == [tech, tech, tech, MULTI_SECTOR_ETF, UNCLASSIFIED, UNCLASSIFIED]


def test_pnl_details_resolve_through_their_instrument_key():
    rows = [
        {"broker_code": "ibkr", "instrument": "vendor_id:265598", "trading_symbol": "NVDA"},
        {"broker_code": "ibkr", "instrument": f"listing_id:{XLK}", "trading_symbol": "XLK"},
        {"broker_code": "ibkr", "instrument": "", "trading_symbol": "XLE", "product_type": "ETF"},
    ]
    assert [value["id"] for value in sectors(rows)] == ["45", "45", "10"]


def test_failed_lookup_names_the_dimension_instead_of_unclassifying():
    rows = [{"listing_id": AAPL, "trading_symbol": "AAPL"}]
    labelled, failed = enrich(Reader(fail_sectors=True), rows, ["sector"])
    assert failed == ["sector"]
    assert labelled[0]["attributes"] == {}


def test_no_dims_issues_no_queries():
    reader = Reader()
    rows = [{"listing_id": AAPL}]
    assert enrich(reader, rows, []) == (rows, [])
    assert reader.queries == []
    assert parse_dims("sector,unknown,sector") == ["sector"]


def test_pnl_groups_label_details_and_excluded():
    groups = [
        {
            "id": "t1",
            "details": [
                {"listing_id": AAPL, "trading_symbol": "AAPL", "account_id": "A"},
                {"listing_id": IWV, "trading_symbol": "IWV", "account_id": "A"},
            ],
            "excluded": [{"trading_symbol": "IWV", "account_id": "A", "issues": []}],
        },
        {"id": "t2", "details": [{"listing_id": XLK, "trading_symbol": "XLK", "account_id": "B"}]},
    ]
    result, failed = with_attributes(Reader(), groups, ["sector"])
    assert failed == []
    assert [d["attributes"]["sector"]["id"] for d in result[0]["details"]] == ["45", "etf"]
    assert result[0]["excluded"][0]["attributes"]["sector"]["id"] == "etf"
    assert result[1]["details"][0]["attributes"]["sector"]["id"] == "45"
    assert result[1]["excluded"] == []


@pytest.fixture
def api():
    class Client:
        def query(self, *args, **kwargs):
            return SimpleNamespace(column_names=[], result_rows=[])

    app.dependency_overrides[repository] = lambda: PortfolioRepository(Client())
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(repository)


def test_dimensions_route_lists_sector_buckets_in_order(api):
    items = api.get("/hub/api/v1/portfolio/dimensions").json()["items"]
    sector = next(item for item in items if item["id"] == "sector")
    ids = [bucket["id"] for bucket in sector["buckets"]]
    assert ids[0] == "10" and ids[-2:] == ["etf", None]
