from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.components.api.portfolio_tagging import Book

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
