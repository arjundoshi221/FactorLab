"""Shape/DDL alignment: adapter facts + storage-owned columns == Wave 7 columns."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from factorlab.sources.ibkr.shapes import (
    AccountStateRow,
    ExecutionRecord,
    OpenOrderSnapshot,
    PositionSnapshot,
    source_channel_for,
)

from .conftest import wave7_columns

NOW = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)

LINEAGE = {"source", "source_channel", "raw_id", "ingest_run_id", "as_of_time",
           "ingested_at", "version"}

# Columns the storage layer (V2BrokerStorage) owns, per table.
STORAGE_OWNED = {
    "broker.positions_snapshot": LINEAGE | {
        "listing_id", "security_id", "contract_id", "entity_id", "resolution_confidence",
        "fx_rate_to_base", "fx_rate_source_time",
        "initial_margin_contribution", "maintenance_margin_contribution",
    },
    "broker.account_state_snapshot": LINEAGE | {"metric_canonical"},
    "broker.executions": LINEAGE | {
        "listing_id", "security_id", "contract_id", "resolution_confidence",
        "execution_method_id", "strategy_id",
    },
    "broker.open_orders_snapshot": LINEAGE | {
        "listing_id", "security_id", "contract_id", "resolution_confidence",
        "execution_method_id", "strategy_id",
    },
}


@pytest.mark.parametrize("dc,table", [
    (PositionSnapshot, "broker.positions_snapshot"),
    (AccountStateRow, "broker.account_state_snapshot"),
    (ExecutionRecord, "broker.executions"),
    (OpenOrderSnapshot, "broker.open_orders_snapshot"),
])
def test_shape_plus_storage_columns_cover_ddl_exactly(dc, table):
    shape = {f.name for f in dataclasses.fields(dc)}
    ddl = wave7_columns(table)
    assert not shape & STORAGE_OWNED[table], "adapter must not own storage columns"
    assert shape | STORAGE_OWNED[table] == ddl, {
        "missing": ddl - shape - STORAGE_OWNED[table],
        "unknown": (shape | STORAGE_OWNED[table]) - ddl,
    }


def _minimum_position(**overrides):
    base = dict(
        snapshot_time=NOW, broker_code="ibkr", account_id="DUE375963",
        account_mode="paper", country_code="US",
        product_type="common", vendor_id="265598", trading_symbol="AAPL",
        currency="USD", position=Decimal("100"),
        avg_cost=Decimal("150"), market_price=Decimal("175.5"),
        market_value=Decimal("17550"), unrealized_pnl=Decimal("2550"),
        realized_pnl_ytd=Decimal("0"), market_value_usd=None,
    )
    base.update(overrides)
    return PositionSnapshot(**base)


def test_position_construction_ok():
    p = _minimum_position()
    assert p.vendor_id == "265598"


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        _minimum_position(snapshot_time=datetime(2026, 9, 19))


def test_dataclass_is_frozen():
    p = _minimum_position()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.trading_symbol = "MSFT"  # type: ignore[misc]


def test_source_channel_for_mode():
    assert source_channel_for("paper") == "paper_gateway"
    assert source_channel_for("live") == "live_gateway"
