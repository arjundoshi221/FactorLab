"""V2BrokerStorage: DDL-exact rows, identity resolution, enrichment and lineage guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from factorlab.shared.ingest.provider import Provenance
from factorlab.sources.ibkr.capture import (
    capture_account_values,
    capture_executions,
    capture_open_orders,
    capture_portfolio,
    decode_capture,
)
from factorlab.sources.ibkr.normalize import (
    normalize_account_state,
    normalize_executions,
    normalize_open_orders,
    normalize_positions,
)
from factorlab.storage.v2_broker import (
    MANUAL_EXECUTION_METHOD,
    UNMAPPED_EXECUTION_METHOD,
    V2BrokerStorage,
)

from .conftest import (
    make_account_value,
    make_contract,
    make_fill,
    make_order,
    make_portfolio_item,
    make_trade,
    wave7_columns,
)

LISTING, SECURITY, ENTITY, CONTRACT = (uuid.uuid4() for _ in range(4))


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    """Fake ClickHouse: AAPL (265598) is an approved listing alias, ES future is a contract."""

    def __init__(self):
        self.inserts = []

    def query(self, sql, parameters=None):
        if "FROM ref.identifier_aliases" in sql:
            rows = [("265598", "listing", LISTING, "exact"),
                    ("495512557", "contract", CONTRACT, "high")]
            return Result([r for r in rows if r[0] in parameters["values"]])
        if "FROM ref.listings AS l" in sql:
            return Result([(LISTING, SECURITY, ENTITY)])
        if "FROM ref.broker_metrics_map" in sql:
            return Result([("NetLiquidation", "net_liquidation")])
        if "FROM ref.execution_methods" in sql:
            return Result([(10, "api_trade_engine_v1")])
        raise AssertionError(sql)

    def insert(self, table, records, column_names):
        self.inserts.append((table, [dict(zip(column_names, row, strict=True)) for row in records]))

    def rows(self, table):
        return [row for name, batch in self.inserts if name == table for row in batch]


@pytest.fixture
def storage():
    s = V2BrokerStorage(Client())
    s._active_run_id = uuid.uuid4()
    return s


def _provenance(storage, now_utc):
    return Provenance(source="ibkr", source_channel="paper_gateway", raw_id=uuid.uuid4(),
                      ingest_run_id=storage._active_run_id, as_of_time=now_utc,
                      ingested_at=now_utc)


def test_positions_rows_match_ddl_and_resolve_identity(storage, mock_ib_paper, now_utc):
    mock_ib_paper.portfolio.return_value = [
        make_portfolio_item(avg_cost=150.123456789),
        make_portfolio_item(contract=make_contract(symbol="ZZZZ", con_id=1)),
    ]
    rows = normalize_positions(decode_capture(capture_portfolio(mock_ib_paper, fetched_at=now_utc).body))
    provenance = _provenance(storage, now_utc)

    assert storage.write_positions(rows, provenance=provenance) == 2
    aapl, unknown = storage.client.rows("broker.positions_snapshot")
    assert set(aapl) == wave7_columns("broker.positions_snapshot")
    assert (aapl["listing_id"], aapl["security_id"], aapl["entity_id"]) == (LISTING, SECURITY, ENTITY)
    assert aapl["resolution_confidence"] == "exact"
    assert aapl["avg_cost"] == Decimal("150.123457")  # quantized to Decimal(20,6)
    assert aapl["raw_id"] == provenance.raw_id
    assert aapl["ingest_run_id"] == storage._active_run_id
    assert aapl["source_channel"] == "paper_gateway"
    assert unknown["listing_id"] is None
    assert unknown["resolution_confidence"] == "unresolved"
    unresolved = {r["alias_value"]: r for r in storage.client.rows("meta.unresolved_entities")}
    assert unresolved["1"]["resolved_at"] is None
    assert unresolved["265598"]["resolved_target_id"] == LISTING


def test_account_state_maps_canonical_metrics(storage, mock_ib_paper, now_utc):
    mock_ib_paper.accountValues.return_value = [
        make_account_value(), make_account_value(tag="Cushion", value="0.5"),
    ]
    rows = normalize_account_state(decode_capture(
        capture_account_values(mock_ib_paper, fetched_at=now_utc).body))
    storage.write_account_state(rows, provenance=_provenance(storage, now_utc))
    nav, cushion = storage.client.rows("broker.account_state_snapshot")
    assert set(nav) == wave7_columns("broker.account_state_snapshot")
    assert nav["metric_canonical"] == "net_liquidation"
    assert cushion["metric_canonical"] == ""


def test_executions_map_execution_method_and_contract_identity(storage, mock_ib_paper, now_utc):
    es = make_contract(sec_type="FUT", con_id=495512557, symbol="ES")
    manual = make_fill()
    manual.execution.clientId = 0
    unmapped = make_fill()
    unmapped.execution.clientId = 77
    mock_ib_paper.reqExecutions.return_value = [make_fill(contract=es), manual, unmapped]
    rows = normalize_executions(decode_capture(
        capture_executions(mock_ib_paper, fetched_at=now_utc).body))
    storage.write_executions(rows, provenance=_provenance(storage, now_utc))
    future, manual_row, unmapped_row = storage.client.rows("broker.executions")
    assert set(future) == wave7_columns("broker.executions")
    assert future["contract_id"] == CONTRACT
    assert future["resolution_confidence"] == "high"
    assert future["execution_method_id"] == "api_trade_engine_v1"
    assert manual_row["execution_method_id"] == MANUAL_EXECUTION_METHOD
    assert unmapped_row["execution_method_id"] == UNMAPPED_EXECUTION_METHOD
    assert future["strategy_id"] is None


def test_open_orders_rows_match_ddl(storage, mock_ib_paper, now_utc):
    mock_ib_paper.openTrades.return_value = [make_trade(order=make_order(client_id=10))]
    rows = normalize_open_orders(decode_capture(
        capture_open_orders(mock_ib_paper, fetched_at=now_utc).body))
    storage.write_open_orders(rows, provenance=_provenance(storage, now_utc))
    [row] = storage.client.rows("broker.open_orders_snapshot")
    assert set(row) == wave7_columns("broker.open_orders_snapshot")
    assert row["execution_method_id"] == "api_trade_engine_v1"


def test_writes_require_the_active_run(storage, mock_ib_paper, now_utc):
    mock_ib_paper.portfolio.return_value = [make_portfolio_item()]
    rows = normalize_positions(decode_capture(capture_portfolio(mock_ib_paper, fetched_at=now_utc).body))
    stale = Provenance(source="ibkr", source_channel="paper_gateway", raw_id=None,
                       ingest_run_id=uuid.uuid4(), as_of_time=now_utc, ingested_at=now_utc)
    with pytest.raises(RuntimeError, match="active ingestion run"):
        storage.write_positions(rows, provenance=stale)


def test_empty_writes_touch_nothing(storage, now_utc):
    provenance = _provenance(storage, now_utc)
    assert storage.write_positions([], provenance=provenance) == 0
    assert storage.write_executions([], provenance=provenance) == 0
    assert storage.client.inserts == []


def test_versions_are_monotonic(storage, mock_ib_paper, now_utc):
    mock_ib_paper.portfolio.return_value = [make_portfolio_item(), make_portfolio_item()]
    rows = normalize_positions(decode_capture(capture_portfolio(mock_ib_paper, fetched_at=now_utc).body))
    storage.write_positions(rows, provenance=_provenance(storage, now_utc))
    versions = [r["version"] for r in storage.client.rows("broker.positions_snapshot")]
    assert versions == sorted(set(versions))
    assert datetime.now(UTC).timestamp() * 1e9 >= versions[0] - 1e9
