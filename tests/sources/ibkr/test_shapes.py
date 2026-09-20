"""Tests for dataclass shape alignment with rehaul §9."""

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
)

NOW = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)


# Columns are pulled straight from docs/architecture/06-schema-rehau.md §9
# (excluding rehaul-internal cols raw_id, ingest_run_id, version — those
# get filled by the ingester at write time, not by the adapter).
POSITION_COLS = {
    "snapshot_time", "broker_code", "account_id", "account_mode", "country_code",
    "listing_id", "security_id", "contract_id", "entity_id", "product_type",
    "vendor_id", "trading_symbol", "currency",
    "position", "avg_cost", "market_price", "market_value",
    "unrealized_pnl", "realized_pnl_ytd", "market_value_usd",
    "resolution_confidence",
    "source", "source_channel",
    "as_of_time", "ingested_at",
}

ACCOUNT_STATE_COLS = {
    "snapshot_time", "broker_code", "account_id", "account_mode", "country_code",
    "metric", "segment", "currency", "value_num", "value_str",
    "source", "source_channel", "as_of_time", "ingested_at",
}

EXECUTION_COLS = {
    "exec_id", "broker_code", "account_id", "account_mode", "country_code",
    "order_id", "perm_id", "placed_by_client",
    "listing_id", "security_id", "contract_id", "entity_id", "product_type",
    "vendor_id", "trading_symbol", "currency",
    "exec_time", "side", "quantity", "price", "exchange", "liquidity_flag",
    "commission", "commission_ccy", "realized_pnl",
    "resolution_confidence", "source", "source_channel",
    "as_of_time", "ingested_at",
}

OPEN_ORDER_COLS = {
    "snapshot_time", "broker_code", "account_id", "account_mode", "country_code",
    "perm_id", "order_id", "placed_by_client",
    "listing_id", "security_id", "contract_id", "entity_id", "product_type",
    "vendor_id", "trading_symbol", "currency",
    "side", "order_type", "time_in_force",
    "quantity", "filled_quantity", "remaining_quantity",
    "limit_price", "aux_price", "status",
    "resolution_confidence", "source", "source_channel",
    "as_of_time", "ingested_at",
}


@pytest.mark.parametrize("dc,expected", [
    (PositionSnapshot, POSITION_COLS),
    (AccountStateRow, ACCOUNT_STATE_COLS),
    (ExecutionRecord, EXECUTION_COLS),
    (OpenOrderSnapshot, OPEN_ORDER_COLS),
])
def test_dataclass_fields_match_rehaul_columns(dc, expected):
    actual = {f.name for f in dataclasses.fields(dc)}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"{dc.__name__} missing fields: {missing}"
    assert not extra, f"{dc.__name__} has unexpected fields: {extra}"


def _minimum_position(**overrides):
    base = dict(
        snapshot_time=NOW, broker_code="ibkr", account_id="DUE375963",
        account_mode="paper", country_code="US",
        listing_id=None, security_id=None, contract_id=None, entity_id=None,
        product_type="common", vendor_id="265598", trading_symbol="AAPL",
        currency="USD", position=Decimal("100"),
        avg_cost=Decimal("150"), market_price=Decimal("175.5"),
        market_value=Decimal("17550"), unrealized_pnl=Decimal("2550"),
        realized_pnl_ytd=Decimal("0"), market_value_usd=None,
        resolution_confidence="unresolved",
        source="ibkr", source_channel="paper_gateway",
        as_of_time=NOW, ingested_at=NOW,
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
