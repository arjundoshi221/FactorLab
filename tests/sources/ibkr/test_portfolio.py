"""Tests for portfolio.snapshot_positions and portfolio.snapshot_account_state."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from factorlab.sources.ibkr.portfolio import snapshot_account_state, snapshot_positions

from .conftest import (
    make_account_value,
    make_contract,
    make_portfolio_item,
)


def test_snapshot_positions_maps_fields(mock_ib_paper, now_utc):
    aapl = make_portfolio_item(
        contract=make_contract(symbol="AAPL", con_id=265598),
        position=100.0, avg_cost=150.0, market_price=175.5,
        market_value=17550.0, unrealized_pnl=2550.0, realized_pnl=42.5,
    )
    spy = make_portfolio_item(
        contract=make_contract(symbol="SPY", con_id=756733, sec_type="STK", primary="ARCA"),
        position=-1079.0, avg_cost=605.87, market_price=762.94,
        market_value=-823212.26, unrealized_pnl=-169469.96, realized_pnl=0.0,
    )
    mock_ib_paper.portfolio.return_value = [aapl, spy]

    rows = snapshot_positions(mock_ib_paper, snapshot_time=now_utc)
    assert len(rows) == 2
    a, s = rows

    assert a.trading_symbol == "AAPL"
    assert a.vendor_id == "265598"
    assert a.product_type == "common"
    assert a.currency == "USD"
    assert a.position == Decimal("100")
    assert a.avg_cost == Decimal("150")
    assert a.market_price == Decimal("175.5")
    assert a.unrealized_pnl == Decimal("2550")
    assert a.realized_pnl_ytd == Decimal("42.5")
    assert a.account_id == "DUE375963"
    assert a.account_mode == "paper"
    assert a.country_code == "US"
    assert a.broker_code == "ibkr"
    assert a.snapshot_time == now_utc
    assert a.market_value_usd is None

    # negative position preserved (short)
    assert s.position == Decimal("-1079")


def test_snapshot_positions_drops_zero_lots(mock_ib_paper, now_utc):
    zero = make_portfolio_item(position=0.0)
    live = make_portfolio_item(position=50.0)
    mock_ib_paper.portfolio.return_value = [zero, live]
    rows = snapshot_positions(mock_ib_paper, snapshot_time=now_utc)
    assert len(rows) == 1
    assert rows[0].position == Decimal("50")


def test_snapshot_positions_handles_none_prices(mock_ib_paper, now_utc):
    # ib_async returns None for market_price when no market-data subscription
    item = make_portfolio_item(market_price=None, market_value=None, unrealized_pnl=None)
    mock_ib_paper.portfolio.return_value = [item]
    rows = snapshot_positions(mock_ib_paper, snapshot_time=now_utc)
    assert rows[0].market_price is None
    assert rows[0].market_value is None
    assert rows[0].unrealized_pnl is None


def test_snapshot_positions_tags_live_mode(mock_ib_live, now_utc):
    mock_ib_live.portfolio.return_value = [make_portfolio_item(account="U18065781")]
    rows = snapshot_positions(mock_ib_live, snapshot_time=now_utc)
    assert rows[0].account_mode == "live"
    assert rows[0].account_id == "U18065781"


def test_snapshot_positions_defaults_snapshot_time():
    ib = _one_position_ib()
    rows = snapshot_positions(ib)
    assert rows[0].snapshot_time.tzinfo is not None


def _one_position_ib():
    from unittest.mock import MagicMock
    ib = MagicMock()
    ib._factorlab_mode = "paper"
    ib.portfolio.return_value = [make_portfolio_item()]
    return ib


def test_snapshot_account_state_maps_segments_and_currency(mock_ib_paper, now_utc):
    vals = [
        make_account_value(tag="NetLiquidation", value="1481501.63", currency="USD"),
        make_account_value(tag="NetLiquidation", value="1481501.63", currency="BASE"),
        make_account_value(tag="BuyingPower", value="4725123.81", currency="USD"),
        make_account_value(tag="Leverage-S", value="0.65", currency=""),  # segment S
        make_account_value(tag="AccountType", value="INDIVIDUAL", currency=""),
    ]
    mock_ib_paper.accountValues.return_value = vals

    rows = snapshot_account_state(mock_ib_paper, snapshot_time=now_utc)
    assert len(rows) == 5
    by_key = {(r.metric, r.segment, r.currency): r for r in rows}

    nav_usd = by_key[("NetLiquidation", "", "USD")]
    assert nav_usd.value_num == Decimal("1481501.63")
    assert nav_usd.value_str is None
    assert nav_usd.account_mode == "paper"

    lev = by_key[("Leverage", "S", "NONE")]
    assert lev.value_num == Decimal("0.65")

    # non-numeric goes to value_str
    acct_type = by_key[("AccountType", "", "NONE")]
    assert acct_type.value_num is None
    assert acct_type.value_str == "INDIVIDUAL"


def test_snapshot_account_state_metric_whitelist(mock_ib_paper, now_utc):
    vals = [
        make_account_value(tag="NetLiquidation", value="100"),
        make_account_value(tag="BuyingPower", value="200"),
        make_account_value(tag="AccountType", value="X", currency=""),
    ]
    mock_ib_paper.accountValues.return_value = vals

    rows = snapshot_account_state(
        mock_ib_paper, snapshot_time=now_utc,
        metrics={"NetLiquidation", "BuyingPower"},
    )
    metrics = {r.metric for r in rows}
    assert metrics == {"NetLiquidation", "BuyingPower"}


def test_snapshot_positions_naive_time_rejected(mock_ib_paper):
    import pytest
    with pytest.raises(ValueError):
        snapshot_positions(mock_ib_paper, snapshot_time=datetime(2026, 9, 19))
