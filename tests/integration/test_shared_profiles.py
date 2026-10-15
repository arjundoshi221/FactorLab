"""Opt-in, local disposable ClickHouse verification of shared profile data flows.

Run with FACTORLAB_PROFILE_INTEGRATION=1 against an empty server on 127.0.0.1:18124
configured with a raw_archive policy. This test never uses environment DB credentials.
"""

import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import clickhouse_connect
import pytest
import yaml
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.components.ingest_broker.profiles import load_registry
from factorlab.components.ingest_broker.stream_simulation import run_stream
from factorlab.ingest.provider import run_provider
from factorlab.schema import codegen
from factorlab.sources.ibkr.simulation import STEPS, simulated_provider
from factorlab.sources.ibkr.stream_simulation import StreamScenario
from factorlab.storage.profiles import ProfileStorage
from factorlab.storage.v2_broker import V2BrokerStorage


@pytest.fixture
def db():
    if os.getenv("FACTORLAB_PROFILE_INTEGRATION") != "1":
        pytest.skip("requires explicitly enabled disposable local ClickHouse")
    client = clickhouse_connect.get_client(
        host="127.0.0.1", port=18124, username="default", database="default"
    )
    existing = client.query(
        "SELECT count() FROM system.tables WHERE database IN ('ref', 'broker', 'meta', 'raw')"
    ).result_rows[0][0]
    assert existing == 0, (
        "test requires an empty disposable server; refusing to modify existing tables"
    )
    for database in ("ref", "broker", "meta", "raw"):
        client.command(f"CREATE DATABASE {database}")
    for wave in (1, 3, 7):
        for _, sql in codegen.collect_tables()[wave]:
            client.command(sql)
    for _, sql in codegen.collect_forward_tables()[(12, "app_profiles")]:
        client.command(sql)
    try:
        yield client
    finally:
        client.close()


def test_two_users_through_archive_storage_and_portfolio_api(db, tmp_path):
    config = Path(__file__).resolve().parents[2] / "configs/testing/broker-profiles.yaml"
    registry = load_registry(config)
    ProfileStorage(db).sync(registry)
    ProfileStorage(db).sync(registry)  # Repeatable registration, no duplicate visible profiles.
    storage = V2BrokerStorage(db)
    repo = PortfolioRepository(db)
    app.dependency_overrides[repository] = lambda: repo
    api = TestClient(app)
    start = datetime.now(UTC) - timedelta(minutes=10)
    try:
        assert len(api.get("/hub/api/v1/portfolio/profiles").json()["items"]) == 2
        for index, step in enumerate(STEPS):
            for account_index, account in enumerate(registry.accounts):
                result = run_provider(
                    simulated_provider(
                        storage,
                        account,
                        step,
                        at=start + timedelta(seconds=index),
                        quantity=10 * (account_index + 1),
                    ),
                    storage,
                    metadata={"simulated": True},
                )
                assert result.status == "success"
                base = f"/hub/api/v1/portfolio/accounts/ibkr/{account.account_id}"
                positions = api.get(base + "/positions")
                assert positions.status_code == 200, positions.text
                orders = api.get(base + "/open-orders").json()
                if step == "filled":
                    assert float(positions.json()["items"][0]["position"]) == 10 * (
                        account_index + 1
                    )
                else:
                    assert positions.json()["items"] == []
                assert len(orders["items"]) == (1 if step == "order" else 0)
                balances = api.get(base + "/summary").json()["balances"]["items"]
                assert all(item["account_id"] == account.account_id for item in balances)

        # A later failed capture must preserve the successful empty snapshot.
        class FailedPositions(V2BrokerStorage):
            def write_positions(self, rows, *, provenance):
                raise RuntimeError("synthetic failure")

        account = registry.accounts[0]
        failed_store = FailedPositions(db)
        result = run_provider(
            simulated_provider(failed_store, account, "filled", at=start + timedelta(seconds=10)),
            failed_store,
        )
        assert result.status == "partial"
        assert repo.snapshot(repo.account("ibkr", account.account_id), "positions").items == []
        # Rerun fills at a later snapshot time, preserving logical execution identity.
        for account_index, account in enumerate(registry.accounts):
            run_provider(
                simulated_provider(
                    storage,
                    account,
                    "closed",
                    at=start + timedelta(seconds=20),
                    quantity=10 * (account_index + 1),
                ),
                storage,
            )
        assert db.query("SELECT count() FROM broker.executions FINAL").result_rows[0][0] == 4
        for user in registry.users:
            owned = api.get(f"/hub/api/v1/portfolio/accounts?user_id={user.user_id}").json()[
                "items"
            ]
            assert len(owned) == 1 and owned[0]["user_id"] == str(user.user_id)
        other_account = registry.accounts[1].account_id
        assert (
            api.get(
                f"/hub/api/v1/portfolio/accounts/ibkr/{other_account}/positions?user_id={registry.users[0].user_id}"
            ).status_code
            == 404
        )
        page = api.get(
            f"/hub/api/v1/portfolio/accounts/ibkr/{other_account}/executions?limit=1"
        ).json()
        assert len(page["items"]) == 1 and page["has_more"]
        body = db.query(
            "SELECT response_body FROM raw.archive WHERE source = 'ibkr_simulation' LIMIT 1",
            column_formats={"response_body": "bytes"},
        ).result_rows[0][0]
        assert json.loads(gzip.decompress(body))["simulated"] is True
        # Timed replay uses a third isolated account and resumes a partial order.
        folder = Path(__file__).resolve().parents[2] / "simulations/ibkr"
        stream_registry = load_registry(folder / "account.yaml")
        stream_scenario = StreamScenario.model_validate(
            yaml.safe_load((folder / "trades.yaml").read_text(encoding="utf-8"))
        )
        ProfileStorage(db).sync(stream_registry)
        checkpoint = tmp_path / "stream.json"
        assert (
            run_stream(
                stream_registry,
                stream_scenario,
                storage,
                session="integration",
                interval=0,
                state_path=checkpoint,
                max_events=3,
            )
            == 0
        )
        base = "/hub/api/v1/portfolio/accounts/ibkr/SIM_IBKR_STREAM"
        partial = api.get(base + "/open-orders").json()["items"]
        assert len(partial) == 1 and float(partial[0]["remaining_quantity"]) == 6
        assert (
            run_stream(
                stream_registry,
                stream_scenario,
                storage,
                session="integration",
                interval=0,
                state_path=checkpoint,
            )
            == 0
        )
        positions = api.get(base + "/positions").json()["items"]
        assert {row["trading_symbol"]: float(row["position"]) for row in positions} == {
            "AAPL": 7,
            "MSFT": 5,
        }
        assert api.get(base + "/open-orders").json()["items"] == []
        balances = api.get(base + "/summary").json()["balances"]["items"]
        cash = next(row["value_num"] for row in balances if row["metric"] == "TotalCashValue")
        assert float(cash) == 96512.60
        assert len(api.get(base + "/executions").json()["items"]) == 4
        completed_runs = db.query("SELECT count() FROM meta.ingestion_runs FINAL").result_rows[0][0]
        run_stream(
            stream_registry,
            stream_scenario,
            storage,
            session="integration",
            interval=0,
            state_path=checkpoint,
        )
        assert (
            db.query("SELECT count() FROM meta.ingestion_runs FINAL").result_rows[0][0]
            == completed_runs
        )
    finally:
        app.dependency_overrides.pop(repository)
