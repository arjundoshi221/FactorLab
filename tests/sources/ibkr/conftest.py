"""Fixtures for IBKR adapter tests. All IB interactions are mocked —
no Gateway is ever contacted."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 9, 19, 20, 30, tzinfo=UTC)


def make_contract(
    *, sec_type: str = "STK", con_id: int = 265598, symbol: str = "AAPL",
    exchange: str = "SMART", primary: str = "NASDAQ", currency: str = "USD",
    local_symbol: str | None = None, trading_class: str = "NMS",
) -> SimpleNamespace:
    return SimpleNamespace(
        secType=sec_type,
        conId=con_id,
        symbol=symbol,
        exchange=exchange,
        primaryExchange=primary,
        currency=currency,
        localSymbol=local_symbol or symbol,
        tradingClass=trading_class,
    )


def make_portfolio_item(
    *, account: str = "DUE375963", contract=None,
    position: float = 100.0, avg_cost: float = 150.0,
    market_price: float | None = 175.5, market_value: float | None = 17550.0,
    unrealized_pnl: float | None = 2550.0, realized_pnl: float | None = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        account=account,
        contract=contract or make_contract(),
        position=position,
        averageCost=avg_cost,
        marketPrice=market_price,
        marketValue=market_value,
        unrealizedPNL=unrealized_pnl,
        realizedPNL=realized_pnl,
    )


def make_account_value(
    *, account: str = "DUE375963", tag: str = "NetLiquidation",
    value: str = "1481501.63", currency: str = "USD",
) -> SimpleNamespace:
    return SimpleNamespace(account=account, tag=tag, value=value, currency=currency, modelCode="")


def make_execution(
    *, exec_id: str = "0001f8a3.66eb01c2.01.01", account: str = "DUE375963",
    order_id: int = 12, perm_id: int = 987654321, client_id: int = 10,
    side: str = "BOT", shares: float = 100.0, price: float = 175.32,
    exchange: str = "NASDAQ", time_: datetime | None = None, last_liquidity: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        execId=exec_id,
        acctNumber=account,
        orderId=order_id,
        permId=perm_id,
        clientId=client_id,
        side=side,
        shares=shares,
        price=price,
        exchange=exchange,
        time=time_ or datetime(2026, 9, 19, 13, 45, tzinfo=UTC),
        lastLiquidity=last_liquidity,
    )


def make_commission_report(
    *, commission: float = 1.05, currency: str = "USD", realized_pnl: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(commission=commission, currency=currency, realizedPNL=realized_pnl)


_UNSET = object()


def make_fill(*, contract=None, execution=None, commission_report=_UNSET) -> SimpleNamespace:
    return SimpleNamespace(
        contract=contract or make_contract(),
        execution=execution or make_execution(),
        commissionReport=make_commission_report() if commission_report is _UNSET else commission_report,
        time=None,
    )


def make_order(
    *, order_id: int = 22, perm_id: int = 111222333, client_id: int = 10,
    account: str = "DUE375963", action: str = "BUY", order_type: str = "LMT",
    tif: str = "DAY", total_quantity: float = 200.0, lmt_price: float | None = 170.0,
    aux_price: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        orderId=order_id,
        permId=perm_id,
        clientId=client_id,
        account=account,
        action=action,
        orderType=order_type,
        tif=tif,
        totalQuantity=total_quantity,
        lmtPrice=lmt_price,
        auxPrice=aux_price,
    )


def make_order_status(
    *, status: str = "Submitted", filled: float = 0.0, remaining: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(status=status, filled=filled, remaining=remaining)


def make_trade(*, contract=None, order=None, order_status=None) -> SimpleNamespace:
    return SimpleNamespace(
        contract=contract or make_contract(),
        order=order or make_order(),
        orderStatus=order_status or make_order_status(),
        fills=[],
    )


@pytest.fixture
def mock_ib_paper() -> MagicMock:
    ib = MagicMock()
    ib._factorlab_mode = "paper"
    ib.isConnected.return_value = True
    ib.portfolio.return_value = []
    ib.accountValues.return_value = []
    ib.reqExecutions.return_value = []
    ib.openTrades.return_value = []
    ib.managedAccounts.return_value = ["DUE375963"]
    return ib


@pytest.fixture
def mock_ib_live() -> MagicMock:
    ib = MagicMock()
    ib._factorlab_mode = "live"
    ib.isConnected.return_value = True
    ib.portfolio.return_value = []
    ib.accountValues.return_value = []
    ib.reqExecutions.return_value = []
    ib.openTrades.return_value = []
    ib.managedAccounts.return_value = ["U18065781"]
    return ib
