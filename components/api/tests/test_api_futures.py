"""US futures curves (F-044): roots, live contracts in expiry order, latest quotes."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.futures import (
    FuturesRepository,
    curves,
    group_of,
    repository,
)

ES = UUID(int=1)
CL = UUID(int=2)
ESZ, ESH, ESM, CLZ = (UUID(int=n) for n in (11, 12, 13, 21))
# A Wednesday 10:00 New York: Globex is open.
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)


def result(columns, values):
    return SimpleNamespace(column_names=columns, result_rows=values)


def responses(observed: datetime = NOW - timedelta(minutes=1)):
    return [
        result(
            ["root", "listing_id", "name", "product_type"],
            [
                ("/CL", CL, "WTI crude oil", "commodity_future"),
                ("/ES", ES, "E-mini S&P 500", "index_future"),
            ],
        ),
        result(
            ["contract_id", "listing_id", "expiry", "symbol"],
            [
                (CLZ, CL, date(2026, 11, 19), "/CLZ26"),
                (ESZ, ES, date(2026, 12, 18), "/ESZ26"),
                (ESH, ES, date(2027, 3, 19), "/ESH27"),
                (ESM, ES, date(2027, 6, 18), "/ESM27"),
            ],
        ),
        result(
            [
                "contract_id",
                "last_price",
                "bid",
                "ask",
                "settlement_price",
                "day_volume",
                "oi",
                "trade_date",
                "last_observed_at",
            ],
            [
                (
                    ESZ,
                    Decimal("6800.25"),
                    Decimal(6800),
                    Decimal("6800.5"),
                    Decimal(6780),
                    1_200_000,
                    2_100_000,
                    date(2026, 10, 21),
                    observed,
                ),
                (ESH, Decimal(6860), None, None, None, 5_000, 40_000, date(2026, 10, 21), observed),
                (
                    CLZ,
                    Decimal("71.5"),
                    Decimal("71.49"),
                    Decimal("71.51"),
                    Decimal(72),
                    300_000,
                    250_000,
                    date(2026, 10, 21),
                    observed.replace(tzinfo=None),
                ),
            ],
        ),
        result(["contract_id", "last_close"], [(ESH, Decimal(6840)), (ESM, Decimal(6900))]),
    ]


def test_group_of_splits_commodities_into_energy_and_metals():
    assert group_of("/CL", "commodity_future") == "energy"
    assert group_of("/GC", "commodity_future") == "metals"
    assert group_of("/ES", "index_future") == "index"
    assert group_of("/ZN", "rate_future") == "rates"
    assert group_of("/6E", "fx_future") == "fx"


def test_curves_join_contracts_with_their_latest_quote():
    client = Mock()
    client.query.side_effect = responses()
    snapshot = curves(FuturesRepository(client), NOW)

    assert snapshot.market_open is True
    assert [(r.root, r.group) for r in snapshot.items] == [("/CL", "energy"), ("/ES", "index")]
    es = snapshot.items[1].contracts
    assert [c.symbol for c in es] == ["/ESZ26", "/ESH27", "/ESM27"]
    front, second, third = es
    assert (front.last, front.settlement_prev, front.change) == (6800.25, 6780.0, 20.25)
    assert front.change_pct is not None and abs(front.change_pct - 20.25 / 6780) < 1e-12
    assert (front.day_volume, front.oi, front.stale) == (1_200_000, 2_100_000, False)
    # No settlement on the quote: the last daily bar's close stands in.
    assert (second.settlement_prev, second.change, second.bid) == (6840.0, 20.0, None)
    # Never quoted: no price, previous close only.
    assert (third.last, third.settlement_prev, third.observed_at) == (None, 6900.0, None)
    # Naive timestamps from the driver are read as UTC.
    observed = snapshot.items[0].contracts[0].observed_at
    assert observed is not None and observed.tzinfo is not None

    sql = [call.args[0] for call in client.query.call_args_list]
    assert "market.futures_quotes" in sql[2] and "GROUP BY contract_id" in sql[2]
    params = client.query.call_args_list[1].kwargs["parameters"]
    assert params["today"] == date(2026, 10, 21) and params["ids"] == [CL, ES]


def test_an_old_quote_is_stale_only_while_globex_is_open():
    client = Mock()
    client.query.side_effect = responses(observed=NOW - timedelta(hours=2))
    snapshot = curves(FuturesRepository(client), NOW)
    assert all(c.stale for c in snapshot.items[1].contracts if c.observed_at)

    saturday = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)
    client.query.side_effect = responses(observed=saturday - timedelta(days=1))
    snapshot = curves(FuturesRepository(client), saturday)
    assert snapshot.market_open is False
    assert not any(c.stale for r in snapshot.items for c in r.contracts)


def test_no_roots_skips_the_contract_and_quote_queries():
    client = Mock()
    client.query.return_value = result(["root", "listing_id", "name", "product_type"], [])
    app.dependency_overrides[repository] = lambda: FuturesRepository(client)
    try:
        response = TestClient(app).get("/hub/api/v1/us/futures")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert client.query.call_count == 1
