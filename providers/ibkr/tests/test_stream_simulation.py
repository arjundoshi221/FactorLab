from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from factorlab.ingest.profiles import ProfileAccount
from factorlab.sources.ibkr.stream_simulation import StreamGateway, StreamScenario

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


def account():
    return ProfileAccount(
        user_id=UUID("9480653e-7863-48f5-b8c1-52a85259d187"),
        broker_code="ibkr",
        account_id="SIM_STREAM",
        account_mode="paper",
        base_currency="USD",
        booking_country="US",
        opened_date=date(2026, 10, 1),
        simulated=True,
    )


def scenario(events):
    return StreamScenario.model_validate(
        {
            "initial_cash": "1000",
            "instruments": {"AAPL": {"con_id": 265598, "initial_price": "10"}},
            "events": events,
        }
    )


def test_partial_fills_average_cost_cash_pnl_and_cancelled_remainder():
    config = scenario(
        [
            {
                "kind": "order",
                "order_id": "buy",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10,
                "limit_price": 12,
            },
            {"kind": "fill", "order_id": "buy", "quantity": 4, "price": 10, "commission": 1},
            {"kind": "fill", "order_id": "buy", "quantity": 6, "price": 11, "commission": 1},
            {
                "kind": "order",
                "order_id": "sell",
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 10,
                "limit_price": 12,
            },
            {"kind": "fill", "order_id": "sell", "quantity": 3, "price": 12, "commission": 1},
            {"kind": "cancel", "order_id": "sell"},
            {"kind": "quote", "symbol": "AAPL", "price": 13},
        ]
    )
    gateway = StreamGateway(account(), config, "demo")
    gateway.apply(config.events[0], 0, NOW)
    gateway.apply(config.events[1], 1, NOW)
    order = gateway.reqAllOpenOrders()[0]
    assert order.orderStatus["filled"] == 4 and order.orderStatus["remaining"] == 6
    for index in range(2, len(config.events)):
        gateway.apply(config.events[index], index, NOW)
    assert gateway.cash == 927
    position = gateway.portfolio()[0]
    assert position["position"] == 7
    assert position["averageCost"] == Decimal("10.8")
    assert position["realizedPNL"] == Decimal("2.6")
    assert position["unrealizedPNL"] == Decimal("15.4")
    values = {row["tag"]: Decimal(row["value"]) for row in gateway.accountValues()}
    assert values["NetLiquidation"] == 1018
    assert gateway.reqAllOpenOrders() == []


@pytest.mark.parametrize(
    "last",
    [
        {"kind": "fill", "order_id": "buy", "quantity": 11, "price": 10},
        {"kind": "fill", "order_id": "buy", "quantity": 1, "price": 13},
        {"kind": "fill", "order_id": "missing", "quantity": 1, "price": 10},
    ],
)
def test_impossible_fills_do_not_change_cash_or_positions(last):
    config = scenario(
        [
            {
                "kind": "order",
                "order_id": "buy",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10,
                "limit_price": 12,
            },
            last,
        ]
    )
    gateway = StreamGateway(account(), config, "demo")
    gateway.apply(config.events[0], 0, NOW)
    with pytest.raises(ValueError):
        gateway.apply(config.events[1], 1, NOW)
    assert gateway.cash == 1000 and gateway.portfolio() == [] and gateway.reqExecutions() == []


def test_stable_fill_ids_and_distinct_sessions():
    config = scenario(
        [
            {
                "kind": "order",
                "order_id": "buy",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "limit_price": 12,
            },
            {"kind": "fill", "order_id": "buy", "quantity": 1, "price": 10},
        ]
    )
    fills = []
    for session in ("demo", "demo", "other"):
        gateway = StreamGateway(account(), config, session)
        for index, event in enumerate(config.events):
            gateway.apply(event, index, NOW)
        fills.append(gateway.reqExecutions()[0])
    assert fills[0].execution == fills[1].execution
    assert fills[0].execution["execId"] != fills[2].execution["execId"]
    assert fills[0].execution["permId"] != fills[2].execution["permId"]


@pytest.mark.parametrize("side,quantity", [("BUY", 100), ("SELL", 1)])
def test_cash_and_long_only_limits(side, quantity):
    config = scenario(
        [
            {
                "kind": "order",
                "order_id": "trade",
                "symbol": "AAPL",
                "side": side,
                "quantity": quantity,
                "limit_price": 12,
            },
            {"kind": "fill", "order_id": "trade", "quantity": quantity, "price": 12},
        ]
    )
    gateway = StreamGateway(account(), config, "demo")
    gateway.apply(config.events[0], 0, NOW)
    with pytest.raises(ValueError, match=r"cash|holdings"):
        gateway.apply(config.events[1], 1, NOW)
    assert gateway.cash == 1000 and gateway.reqExecutions() == []


def test_full_close_clears_positions_and_net_liquidation_includes_both_fees():
    config = scenario(
        [
            {
                "kind": "order",
                "order_id": "buy",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "limit_price": 10,
            },
            {"kind": "fill", "order_id": "buy", "quantity": 1, "price": 10, "commission": 1},
            {
                "kind": "order",
                "order_id": "sell",
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 1,
                "limit_price": 12,
            },
            {"kind": "fill", "order_id": "sell", "quantity": 1, "price": 12, "commission": 1},
        ]
    )
    gateway = StreamGateway(account(), config, "demo")
    for index, event in enumerate(config.events):
        gateway.apply(event, index, NOW)
    assert gateway.portfolio() == [] and gateway.reqAllOpenOrders() == []
    values = {row["tag"]: Decimal(row["value"]) for row in gateway.accountValues()}
    assert values["NetLiquidation"] == 1000 and values["RealizedPnL"] == 0
