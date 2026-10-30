from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository

USER = "d677331a-a04f-454a-8a1e-02425be2c101"


class Client:
    def __init__(self, base_currency="USD"):
        self.base_currency = base_currency
        self.calls = []

    def query(self, sql, parameters=None, **_kwargs):
        parameters = parameters or {}
        self.calls.append((sql, parameters))
        if "FROM ref.app_users" in sql:
            values = [{"user_id": USER, "display_name": "A", "active": True}]
        elif "FROM ref.broker_account_owners" in sql:
            values = [
                {"broker_code": "ibkr", "account_id": "U1", "user_id": USER, "simulated": False}
            ]
        elif "FROM ref.broker_accounts" in sql:
            values = [
                {
                    "broker_code": "ibkr",
                    "account_id": "U1",
                    "account_mode": "live",
                    "base_currency": self.base_currency,
                }
            ]
        elif "GROUP BY broker_code, account_id" in sql:
            values = []
        elif "argMax(value_num, snapshot_time)" in sql:
            values = [
                {"t": datetime(2026, 10, 1), "value": Decimal("100000.125000")},
                {"t": datetime(2026, 10, 2, tzinfo=UTC), "value": Decimal("101250.500000")},
            ]
        else:
            values = []
        columns = list(values[0]) if values else []
        return SimpleNamespace(
            column_names=columns,
            result_rows=[tuple(item[name] for name in columns) for item in values],
        )


@pytest.fixture
def make_api():
    def build(**kwargs):
        db = Client(**kwargs)
        app.dependency_overrides[repository] = lambda: PortfolioRepository(db)
        return TestClient(app), db

    yield build
    app.dependency_overrides.pop(repository, None)


def test_equity_returns_daily_base_currency_net_liquidation(make_api):
    client, db = make_api()
    response = client.get(f"/hub/api/v1/portfolio/accounts/ibkr/U1/equity?user_id={USER}&range=1y")
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD"
    assert body["resolution"] == "day"
    assert [point["value"] for point in body["points"]] == ["100000.125000", "101250.500000"]
    assert body["points"][0]["t"].startswith("2026-10-01T00:00:00")
    sql, params = next(call for call in db.calls if "argMax(value_num" in call[0])
    assert "segment = ''" in sql and "currency = {currency:String}" in sql and "toStartOfDay" in sql
    assert params["currency"] == "USD" and "since" in params


def test_equity_one_month_is_hourly_and_max_has_no_lower_bound(make_api):
    client, db = make_api()
    assert (
        client.get("/hub/api/v1/portfolio/accounts/ibkr/U1/equity?range=1m").json()["resolution"]
        == "hour"
    )
    assert "toStartOfHour" in [call for call in db.calls if "argMax(value_num" in call[0]][-1][0]
    client.get("/hub/api/v1/portfolio/accounts/ibkr/U1/equity?range=max")
    sql, params = [call for call in db.calls if "argMax(value_num" in call[0]][-1]
    assert "since" not in params and "snapshot_time >=" not in sql


def test_equity_without_base_currency_is_empty_not_summed(make_api):
    client, db = make_api(base_currency=None)
    body = client.get("/hub/api/v1/portfolio/accounts/ibkr/U1/equity").json()
    assert body["points"] == [] and body["currency"] is None
    assert not any("argMax(value_num" in sql for sql, _ in db.calls)


def test_equity_rejects_unknown_range_and_foreign_profile(make_api):
    client, _ = make_api()
    assert client.get("/hub/api/v1/portfolio/accounts/ibkr/U1/equity?range=5y").status_code == 422
    other = "d677331a-a04f-454a-8a1e-02425be2c199"
    assert (
        client.get(f"/hub/api/v1/portfolio/accounts/ibkr/U1/equity?user_id={other}").status_code
        == 404
    )
