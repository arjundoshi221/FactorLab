from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.components.api.portfolio_tagging import Book, period_start

USER = "d677331a-a04f-454a-8a1e-02425be2c101"


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


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("catalogs/themes", {"name": " "}),
        ("catalogs/unknown", {"name": "x"}),
        ("catalogs/themes", {"name": "x", "user_id": USER}),
    ],
)
def test_catalog_inputs_are_validated_before_writes(api, path, body):
    assert api.post(f"/hub/api/v1/portfolio/{path}?user_id={USER}", json=body).status_code == 422


def test_assignment_triplet_validation_and_cross_site_guard(api):
    url = f"/hub/api/v1/portfolio/accounts/test/A/orders/1/assignment?user_id={USER}"
    assert api.put(url, json={"theme_id": USER}).status_code == 422
    assert api.put(url, json={}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert api.get("/hub/api/v1/portfolio/catalogs/themes?user_id=unassigned").status_code == 422


def test_zero_order_ids_never_group_unrelated_executions(monkeypatch):
    class Repo:
        def query(self, sql, *args):
            if "order_assignment_events" in sql:
                return []
            return [
                {
                    "broker_code": "test",
                    "account_id": "A",
                    "account_mode": "paper",
                    "perm_id": 0,
                    "exec_id": str(i),
                    "exec_time": "2026-10-15T12:00:00Z",
                    "quantity": 1,
                    "trading_symbol": "ABC",
                    "currency": "USD",
                    "side": "BUY",
                }
                for i in range(2)
            ]

        def snapshot(self, *_args):
            return SimpleNamespace(items=[])

    reader = PortfolioRepository(None)
    fake = Repo()
    monkeypatch.setattr(reader, "query", fake.query)
    monkeypatch.setattr(reader, "snapshot", fake.snapshot)
    orders = Book(reader).orders(
        {"broker_code": "test", "account_id": "A", "user_id": USER, "simulated": True}
    )
    assert len(orders) == 2 and not any(row["taggable"] for row in orders)


def test_account_fees_are_positive_costs_per_currency(monkeypatch):
    seen = {}

    def query(sql, params=None):
        seen.update(params or {})
        if "broker.executions" in sql:
            assert (params or {})["fx"] == ["CASH", "forex"]
            return [{"currency": "SGD", "amount": "2.5684"}]
        return [
            {"currency": "USD", "cash_flow_type": "fee", "amount": "-10.5", "events": 2},
            {"currency": "USD", "cash_flow_type": "commission", "amount": "1.25", "events": 1},
        ]

    reader = PortfolioRepository(None)
    monkeypatch.setattr(reader, "query", query)
    since = period_start(date(2026, 1, 1))
    rows = Book(reader).account_fees(
        [{"broker_code": "ibkr", "account_id": "U1", "account_mode": "live", "simulated": False}],
        since,
    )
    assert rows == [
        {
            "broker_code": "ibkr",
            "account_id": "U1",
            "account_mode": "live",
            "simulated": False,
            "currency": "SGD",
            "fees": "0",
            "commission_adjustments": "0",
            "fx_conversions": "2.5684",
            "total": "2.5684",
        },
        {
            "broker_code": "ibkr",
            "account_id": "U1",
            "account_mode": "live",
            "simulated": False,
            "currency": "USD",
            "fees": "10.5",
            "commission_adjustments": "-1.25",
            "fx_conversions": "0",
            "total": "9.25",
        },
    ]
    # YTD starts at New York midnight, not UTC midnight.
    assert seen["since"] == datetime(2026, 1, 1, 5, tzinfo=UTC)


def test_pnl_route_reports_period_and_rejects_bad_dates(api):
    url = f"/hub/api/v1/portfolio/pnl/themes?user_id={USER}"
    assert api.get(f"{url}&since=2026-13-01").status_code == 422


def test_fx_rates_quote_each_currency_in_its_account_base(monkeypatch):
    seen = {}

    def query(sql, params=None):
        seen.update(params or {})
        assert "ExchangeRate" in sql
        return [
            {
                "broker_code": "ibkr",
                "account_id": "U1",
                "currency": "USD",
                "rate": "1.29",
                "as_of": "t",
            },
            {
                "broker_code": "ibkr",
                "account_id": "U1",
                "currency": "SGD",
                "rate": "1",
                "as_of": "t",
            },
            {
                "broker_code": "ibkr",
                "account_id": "U1",
                "currency": "GBP",
                "rate": None,
                "as_of": "t",
            },
        ]

    reader = PortfolioRepository(None)
    monkeypatch.setattr(reader, "query", query)
    accounts = [
        {"broker_code": "ibkr", "account_id": "U1", "base_currency": "SGD"},
        {"broker_code": "ibkr", "account_id": "U2", "base_currency": ""},  # no base: no rates
    ]
    assert Book(reader).fx_rates(accounts) == [
        {"currency": "SGD", "base": "SGD", "rate": "1", "as_of": None},
        {"currency": "USD", "base": "SGD", "rate": "1.29", "as_of": "t"},
    ]
    assert seen["accounts"] == [("ibkr", "U1")]
    assert Book(reader).fx_rates([accounts[1]]) == []
