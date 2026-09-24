"""Tests for open_orders.snapshot_open_orders."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from factorlab.sources.ibkr.open_orders import snapshot_open_orders

from .conftest import make_contract, make_order, make_order_status, make_trade


def test_empty_open_orders(mock_ib_paper, now_utc):
    mock_ib_paper.openTrades.return_value = []
    rows = snapshot_open_orders(mock_ib_paper, snapshot_time=now_utc)
    assert rows == []


def test_snapshot_open_orders_maps_fields(mock_ib_paper, now_utc):
    trade = make_trade(
        contract=make_contract(symbol="AAPL", con_id=265598),
        order=make_order(
            order_id=42, perm_id=555_111_222, client_id=10, account="DUE375963",
            action="BUY", order_type="LMT", tif="DAY",
            total_quantity=200.0, lmt_price=170.0, aux_price=None,
        ),
        order_status=make_order_status(status="Submitted", filled=50.0, remaining=150.0),
    )
    mock_ib_paper.openTrades.return_value = [trade]

    rows = snapshot_open_orders(mock_ib_paper, snapshot_time=now_utc)
    assert len(rows) == 1
    r = rows[0]
    assert r.trading_symbol == "AAPL"
    assert r.vendor_id == "265598"
    assert r.perm_id == 555_111_222
    assert r.order_id == 42
    assert r.placed_by_client == 10
    assert r.side == "BUY"
    assert r.order_type == "LMT"
    assert r.time_in_force == "DAY"
    assert r.quantity == Decimal("200")
    assert r.filled_quantity == Decimal("50")
    assert r.remaining_quantity == Decimal("150")
    assert r.limit_price == Decimal("170")
    assert r.aux_price is None
    assert r.status == "Submitted"
    assert r.account_id == "DUE375963"
    assert r.account_mode == "paper"
    assert r.snapshot_time == now_utc


def test_snapshot_open_orders_computes_remaining_when_missing(mock_ib_paper, now_utc):
    trade = make_trade(
        order=make_order(total_quantity=100.0),
        order_status=make_order_status(filled=30.0, remaining=None),
    )
    mock_ib_paper.openTrades.return_value = [trade]
    rows = snapshot_open_orders(mock_ib_paper, snapshot_time=now_utc)
    assert rows[0].remaining_quantity == Decimal("70")


def test_snapshot_open_orders_naive_time_rejected(mock_ib_paper):
    with pytest.raises(ValueError):
        snapshot_open_orders(mock_ib_paper, snapshot_time=datetime(2026, 9, 19))


def test_snapshot_open_orders_tags_live_mode(mock_ib_live, now_utc):
    mock_ib_live.openTrades.return_value = [make_trade()]
    rows = snapshot_open_orders(mock_ib_live, snapshot_time=now_utc)
    assert rows[0].account_mode == "live"
