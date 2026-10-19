from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository

USER_A = "d677331a-a04f-454a-8a1e-02425be2c101"
USER_B = "d677331a-a04f-454a-8a1e-02425be2c102"
NOW = datetime.now(UTC)


class Client:
    def __init__(self):
        self.calls = []
        self.empty = False

    def query(self, sql, parameters=None, **_kwargs):
        parameters = parameters or {}
        self.calls.append((sql, parameters))
        if "FROM ref.app_users" in sql:
            values = [
                {"user_id": USER_A, "display_name": "A", "active": True},
                {"user_id": USER_B, "display_name": "B", "active": True},
            ]
        elif "FROM ref.broker_account_owners" in sql:
            values = [
                {
                    "broker_code": "ibkr",
                    "account_id": "SIM_A",
                    "user_id": USER_A,
                    "simulated": True,
                },
                {
                    "broker_code": "ibkr",
                    "account_id": "SIM_B",
                    "user_id": USER_B,
                    "simulated": True,
                },
            ]
        elif "FROM ref.broker_accounts" in sql:
            values = [
                {"broker_code": "ibkr", "account_id": name, "account_mode": "paper"}
                for name in ("SIM_A", "SIM_B", "OLD")
            ]
        elif "GROUP BY broker_code, account_id" in sql:
            values = []
        elif "JSONExtractArrayRaw" in sql:
            values = [{"snapshot_time": NOW, "source_channel": "simulation"}]
        elif "FROM broker.executions" in sql:
            values = [{"account_id": parameters["account"], "exec_id": str(i)} for i in range(3)]
        else:
            values = (
                []
                if self.empty
                else [
                    {"account_id": parameters["account"], "trading_symbol": "AAPL", "position": 10}
                ]
            )
        columns = list(values[0]) if values else []
        return SimpleNamespace(
            column_names=columns,
            result_rows=[tuple(item[name] for name in columns) for item in values],
        )


@pytest.fixture
def api():
    db = Client()
    app.dependency_overrides[repository] = lambda: PortfolioRepository(db)
    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.pop(repository)


def test_profile_filter_and_cross_profile_account_rejection(api):
    client, _ = api
    response = client.get(f"/hub/api/v1/portfolio/accounts?user_id={USER_A}")
    assert [item["account_id"] for item in response.json()["items"]] == ["SIM_A"]
    assert (
        client.get(
            f"/hub/api/v1/portfolio/accounts/ibkr/SIM_B/positions?user_id={USER_A}"
        ).status_code
        == 404
    )
    assert (
        client.get("/hub/api/v1/portfolio/accounts?user_id=unassigned").json()["items"][0][
            "account_id"
        ]
        == "OLD"
    )
    assert client.get("/hub/api/v1/portfolio/accounts?user_id=invalid").status_code == 422


def test_successful_empty_snapshot_clears_old_holdings(api):
    client, db = api
    db.empty = True
    response = client.get("/hub/api/v1/portfolio/accounts/ibkr/SIM_A/positions")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["snapshot_time"] is not None
    assert not any("ORDER BY snapshot_time DESC LIMIT 1" in sql for sql, _ in db.calls)


def test_executions_are_bounded_and_query_uses_full_account_identity(api):
    client, db = api
    response = client.get("/hub/api/v1/portfolio/accounts/ibkr/SIM_A/executions?limit=2")
    assert response.json()["has_more"] is True
    assert len(response.json()["items"]) == 2
    sql, params = next(
        (sql, params) for sql, params in db.calls if "SELECT * FROM broker.executions" in sql
    )
    assert " FINAL " in sql and "broker_code = {broker:String}" in sql
    assert params["account"] == "SIM_A" and params["broker"] == "ibkr"
    assert (
        client.get("/hub/api/v1/portfolio/accounts/ibkr/SIM_A/executions?limit=201").status_code
        == 422
    )
