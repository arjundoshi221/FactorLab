"""Opt-in test on an EMPTY disposable ClickHouse at 127.0.0.1:18125.

Set FACTORLAB_PORTFOLIO_INTEGRATION=1. No production settings or credentials are read.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import clickhouse_connect
import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.schema import codegen

A = UUID("d677331a-a04f-454a-8a1e-02425be2c101")
B = UUID("d677331a-a04f-454a-8a1e-02425be2c102")
PREFIX = "/hub/api/v1/portfolio"
KINDS = ("themes", "strategies", "trade-names")


def insert(db, table, **record):
    db.insert(table, [list(record.values())], column_names=list(record))


@pytest.fixture(scope="module")
def db():
    if os.getenv("FACTORLAB_PORTFOLIO_INTEGRATION") != "1":
        pytest.skip("set FACTORLAB_PORTFOLIO_INTEGRATION=1 for disposable local ClickHouse")
    client = clickhouse_connect.get_client(
        host="127.0.0.1", port=18125, username="default", password=""
    )
    assert client.query(
        "SELECT count() FROM system.tables WHERE database IN ('ref','broker','book','meta')"
    ).result_rows == [(0,)], "Requires empty disposable server"
    for database in ("ref", "broker", "book", "meta"):
        client.command(f"CREATE DATABASE {database}")
    wanted = {
        "ref.broker_accounts",
        "broker.executions",
        "broker.account_state_snapshot",
        "broker.positions_snapshot",
        "broker.open_orders_snapshot",
        "meta.ingestion_runs",
        "book.themes",
        "book.strategies",
        "book.trade_names",
    }
    for statements in codegen.collect_tables().values():
        for name, sql in statements:
            if name in wanted:
                client.command(sql)
    forward = codegen.collect_forward_tables()
    for key in ((12, "app_profiles"), (13, "portfolio_tagging")):
        for _, sql in forward[key]:
            client.command(sql)
    # Forward schema is safe to retry after interruption.
    for _, sql in forward[13, "portfolio_tagging"]:
        client.command(sql)
    now = datetime.now(UTC)
    for user, account in ((A, "SIM_A"), (B, "SIM_B")):
        insert(
            client,
            "ref.app_users",
            user_id=user,
            display_name=account,
            active=True,
            version=1,
            ingested_at=now,
        )
        insert(
            client,
            "ref.broker_accounts",
            broker_code="ibkr",
            account_id=account,
            account_mode="paper",
            base_currency="USD",
            booking_country="US",
            active=True,
            version=1,
            ingested_at=now,
        )
        insert(
            client,
            "ref.broker_account_owners",
            broker_code="ibkr",
            account_id=account,
            user_id=user,
            simulated=True,
            version=1,
            ingested_at=now,
        )
    yield client
    client.close()


def test_catalog_orders_future_fills_retagging_and_pnl(db):
    app.dependency_overrides[repository] = lambda: PortfolioRepository(db)
    client = TestClient(app)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        entries = {}
        for kind in KINDS:
            response = client.post(
                f"{PREFIX}/catalogs/{kind}?user_id={A}",
                json={"name": f"A {kind}", "description": "Independent"},
            )
            assert response.status_code == 201, response.text
            entries[kind] = response.json()["id"]
            assert len(client.get(f"{PREFIX}/catalogs/{kind}?user_id={A}").json()["items"]) == 1
            assert client.get(f"{PREFIX}/catalogs/{kind}?user_id={B}").json()["items"] == []
        tags = dict(
            zip(("theme_id", "strategy_id", "trade_name_id"), entries.values(), strict=True)
        )
        assert db.query("SELECT theme_id, strategy_id FROM book.trade_names FINAL").result_rows == [
            (None, None)
        ]
        base = f"{PREFIX}/accounts/ibkr/SIM_A/orders"

        # Session ID is deliberately reused across two durable orders.
        def fill(exec_id, perm, side, qty, price, at, version=1):
            insert(
                db,
                "broker.executions",
                broker_code="ibkr",
                account_id="SIM_A",
                account_mode="paper",
                country_code="US",
                exec_id=exec_id,
                perm_id=perm,
                order_id=7,
                exec_time=at,
                vendor_id="100",
                trading_symbol="ABC",
                product_type="common",
                currency="USD",
                side=side,
                quantity=qty,
                price=price,
                commission=1,
                commission_ccy="USD",
                version=version,
            )

        fill("1", 100, "BUY", 10, 100, now - timedelta(minutes=10))
        assert client.put(f"{base}/100/assignment?user_id={A}", json=tags).status_code == 200
        fill("2", 100, "BUY", 10, 110, now - timedelta(minutes=9))
        fill("2", 100, "BUY", 10, 110, now - timedelta(minutes=9), version=2)
        fill("3", 101, "SELL", 15, 130, now - timedelta(minutes=8))
        assert client.put(f"{base}/101/assignment?user_id={A}", json=tags).status_code == 200
        insert(
            db,
            "broker.positions_snapshot",
            broker_code="ibkr",
            account_id="SIM_A",
            account_mode="paper",
            country_code="US",
            snapshot_time=now,
            vendor_id="100",
            trading_symbol="ABC",
            product_type="common",
            currency="USD",
            position=5,
            market_price=120,
            version=1,
            source_channel="test",
        )
        response = client.get(f"{base}?user_id={A}")
        assert response.status_code == 200, response.text
        orders = response.json()["items"]
        assert len(orders) == 2
        opening = next(row for row in orders if row["perm_id"] == "100")
        assert len(opening["fills"]) == 2 and opening["theme_id"] == tags["theme_id"]
        assert all(row["trade_name_id"] == tags["trade_name_id"] for row in opening["fills"])
        for kind in KINDS:
            response = client.get(f"{PREFIX}/pnl/{kind}?user_id={A}")
            assert response.status_code == 200, response.text
            row = response.json()["items"][0]
            assert row["realized"] == "400.000000000000" and row["unrealized"] == "50.000000000000"
            assert float(row["total"]) == 447
        # All-or-none, profile ownership, durable IDs, unknown orders and cross-site writes.
        assert (
            client.put(
                f"{base}/100/assignment?user_id={A}", json={"theme_id": tags["theme_id"]}
            ).status_code
            == 422
        )
        assert client.put(f"{base}/100/assignment?user_id={B}", json=tags).status_code == 404
        assert client.put(f"{base}/0/assignment?user_id={A}", json=tags).status_code == 422
        assert client.put(f"{base}/999/assignment?user_id={A}", json=tags).status_code == 404
        assert (
            client.post(
                f"{PREFIX}/catalogs/themes?user_id={A}",
                json={"name": "bad"},
                headers={"origin": "https://other.example"},
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"{PREFIX}/catalogs/themes/{entries['themes']}?user_id={B}", json={"name": "bad"}
            ).status_code
            == 404
        )
        # Move both orders to a new theme; independent strategy/name stay unchanged.
        new_theme = client.post(
            f"{PREFIX}/catalogs/themes?user_id={A}", json={"name": "New"}
        ).json()["id"]
        tags["theme_id"] = new_theme
        for perm in (100, 101):
            assert client.put(f"{base}/{perm}/assignment?user_id={A}", json=tags).status_code == 200
        moved = client.get(f"{PREFIX}/pnl/themes?user_id={A}").json()["items"]
        assert len(moved) == 1 and moved[0]["id"] == new_theme and float(moved[0]["total"]) == 447
        history = client.get(f"{base}/100/assignment-history?user_id={A}").json()["items"]
        assert len(history) == 2
        assert client.put(f"{base}/100/assignment?user_id={A}", json={}).status_code == 200
        assert (
            next(
                row
                for row in client.get(f"{base}?user_id={A}").json()["items"]
                if row["perm_id"] == "100"
            )["theme_id"]
            is None
        )
        assert (
            client.put(
                f"{PREFIX}/catalogs/themes/{new_theme}?user_id={A}",
                json={"name": "New", "active": False},
            ).status_code
            == 200
        )
        assert client.put(f"{base}/100/assignment?user_id={A}", json=tags).status_code == 422
        assert len(client.get(f"{base}/101/assignment-history?user_id={A}").json()["items"]) == 2
    finally:
        app.dependency_overrides.pop(repository)
