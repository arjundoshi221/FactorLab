"""Scripted, stateful IBKR-shaped responses for an isolated synthetic account."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from factorlab.ingest.profiles import ProfileAccount
from factorlab.sources.ibkr.provider import SnapshotConfig
from factorlab.sources.ibkr.shapes import Mode
from factorlab.sources.ibkr.simulation import SimulatedBrokerProvider

if TYPE_CHECKING:
    from ib_async import IB

Money = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Quantity = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Instrument(StrictModel):
    con_id: int = Field(gt=0)
    initial_price: Money


class QuoteEvent(StrictModel):
    kind: Literal["quote"]
    symbol: str
    price: Money


class OrderEvent(StrictModel):
    kind: Literal["order"]
    order_id: str = Field(min_length=1, max_length=64)
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Quantity
    limit_price: Money


class FillEvent(StrictModel):
    kind: Literal["fill"]
    order_id: str
    quantity: Quantity
    price: Money
    commission: Decimal = Field(default=Decimal("0.35"), ge=0, allow_inf_nan=False)


class CancelEvent(StrictModel):
    kind: Literal["cancel"]
    order_id: str


Event = Annotated[QuoteEvent | OrderEvent | FillEvent | CancelEvent, Field(discriminator="kind")]


class StreamScenario(StrictModel):
    initial_cash: Money = Decimal(100000)
    instruments: dict[str, Instrument] = Field(min_length=1)
    events: list[Event] = Field(min_length=1)


@dataclass
class Holding:
    quantity: Decimal = Decimal(0)
    average_cost: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)


@dataclass
class WorkingOrder:
    event: OrderEvent
    number: int
    filled: Decimal = Decimal(0)
    cancelled: bool = False

    @property
    def remaining(self) -> Decimal:
        return self.event.quantity - self.filled


class StreamGateway:
    """Long-only USD cash ledger; no sockets or broker SDK mutation methods."""

    _factorlab_mode = "paper"
    RequestTimeout = 15.0

    def __init__(self, account: ProfileAccount, scenario: StreamScenario, session: str) -> None:
        if not account.simulated or not account.active or account.broker_code != "ibkr":
            raise ValueError("stream requires an active synthetic IBKR account")
        if (
            account.account_mode != "paper"
            or account.base_currency != "USD"
            or account.booking_country != "US"
        ):
            raise ValueError("stream supports only US-booked USD paper accounts")
        self.account = account
        self.scenario = scenario
        self.session = session
        self.cash = scenario.initial_cash
        self.prices = {symbol: item.initial_price for symbol, item in scenario.instruments.items()}
        self.holdings: dict[str, Holding] = {}
        self.orders: dict[str, WorkingOrder] = {}
        self.fills: list[SimpleNamespace] = []
        self.connected = True

    def managedAccounts(self) -> list[str]:
        return [self.account.account_id]

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False

    def contract(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.scenario.instruments:
            raise ValueError(f"unknown instrument: {symbol}")
        return {
            "conId": self.scenario.instruments[symbol].con_id,
            "symbol": symbol,
            "secType": "STK",
            "currency": "USD",
            "exchange": "SMART",
        }

    def apply(self, event: Event, index: int, at: datetime) -> None:
        if isinstance(event, QuoteEvent):
            self.contract(event.symbol)
            self.prices[event.symbol] = event.price
            return
        if isinstance(event, OrderEvent):
            self.contract(event.symbol)
            if event.order_id in self.orders:
                raise ValueError(f"duplicate order: {event.order_id}")
            self.orders[event.order_id] = WorkingOrder(event, index + 1)
            return
        order = self.orders.get(event.order_id)
        if order is None or order.cancelled or order.remaining == 0:
            raise ValueError(f"order is not working: {event.order_id}")
        if isinstance(event, CancelEvent):
            order.cancelled = True
            return
        if event.quantity > order.remaining:
            raise ValueError("fill exceeds remaining order quantity")
        buying = order.event.side == "BUY"
        if (buying and event.price > order.event.limit_price) or (
            not buying and event.price < order.event.limit_price
        ):
            raise ValueError("fill violates the order limit price")
        symbol = order.event.symbol
        holding = self.holdings.get(symbol, Holding())
        cost = event.quantity * event.price
        realized = Decimal(0)
        if buying:
            if cost + event.commission > self.cash:
                raise ValueError("insufficient simulated cash")
            holding.average_cost = (
                holding.quantity * holding.average_cost + cost + event.commission
            ) / (holding.quantity + event.quantity)
            holding.quantity += event.quantity
            self.cash -= cost + event.commission
        else:
            if event.quantity > holding.quantity:
                raise ValueError("sell exceeds simulated holdings; short selling is not supported")
            realized = (event.price - holding.average_cost) * event.quantity - event.commission
            holding.quantity -= event.quantity
            holding.realized += realized
            self.cash += cost - event.commission
            if holding.quantity == 0:
                holding.average_cost = Decimal(0)
        self.holdings[symbol] = holding
        self.prices[symbol] = event.price
        order.filled += event.quantity
        digest = hashlib.sha256(f"{self.account.account_id}:{self.session}".encode()).hexdigest()[
            :16
        ]
        durable_number = int(
            hashlib.sha256(f"{digest}:{order.number}".encode()).hexdigest()[:14], 16
        )
        self.fills.append(
            SimpleNamespace(
                contract=self.contract(symbol),
                time=at,
                execution={
                    "execId": f"SIM.{digest}.{index}",
                    "acctNumber": self.account.account_id,
                    "orderId": order.number,
                    "permId": durable_number,
                    "clientId": 0,
                    "orderRef": f"sim:{self.session}:{event.order_id}",
                    "time": at,
                    "side": "BOT" if buying else "SLD",
                    "shares": event.quantity,
                    "price": event.price,
                    "exchange": "SIMULATED",
                    "lastLiquidity": 1,
                },
                commissionReport={
                    "commission": event.commission,
                    "currency": "USD",
                    "realizedPNL": realized,
                },
            )
        )

    def portfolio(self) -> list[dict[str, Any]]:
        return [
            {
                "account": self.account.account_id,
                "contract": self.contract(symbol),
                "position": item.quantity,
                "averageCost": item.average_cost,
                "marketPrice": self.prices[symbol],
                "marketValue": item.quantity * self.prices[symbol],
                "unrealizedPNL": item.quantity * (self.prices[symbol] - item.average_cost),
                "realizedPNL": item.realized,
            }
            for symbol, item in self.holdings.items()
            if item.quantity
        ]

    def accountValues(self) -> list[dict[str, Any]]:
        value = sum(
            (item.quantity * self.prices[symbol] for symbol, item in self.holdings.items()),
            Decimal(0),
        )
        unrealized = sum(
            (
                item.quantity * (self.prices[symbol] - item.average_cost)
                for symbol, item in self.holdings.items()
            ),
            Decimal(0),
        )
        realized = sum((item.realized for item in self.holdings.values()), Decimal(0))
        return [
            {
                "account": self.account.account_id,
                "tag": tag,
                "value": str(number),
                "currency": "USD",
            }
            for tag, number in (
                ("NetLiquidation", self.cash + value),
                ("TotalCashValue", self.cash),
                ("BuyingPower", self.cash),
                ("UnrealizedPnL", unrealized),
                ("RealizedPnL", realized),
            )
        ]

    def reqAllOpenOrders(self) -> list[SimpleNamespace]:
        digest = hashlib.sha256(f"{self.account.account_id}:{self.session}".encode()).hexdigest()[
            :16
        ]
        return [
            SimpleNamespace(
                contract=self.contract(order.event.symbol),
                order={
                    "account": self.account.account_id,
                    "orderId": order.number,
                    "permId": int(
                        hashlib.sha256(f"{digest}:{order.number}".encode()).hexdigest()[:14], 16
                    ),
                    "clientId": 0,
                    "orderRef": f"sim:{self.session}:{order.event.order_id}",
                    "action": order.event.side,
                    "orderType": "LMT",
                    "tif": "DAY",
                    "totalQuantity": order.event.quantity,
                    "lmtPrice": order.event.limit_price,
                },
                orderStatus={
                    "status": "Submitted",
                    "filled": order.filled,
                    "remaining": order.remaining,
                },
            )
            for order in self.orders.values()
            if not order.cancelled and order.remaining
        ]

    def reqExecutions(self, filter_: Any = None) -> list[SimpleNamespace]:
        since = getattr(filter_, "time", "")
        if not since:
            return self.fills[:]
        cutoff = datetime.strptime(since, "%Y%m%d-%H:%M:%S").replace(tzinfo=UTC)
        return [fill for fill in self.fills if fill.time >= cutoff]


def stream_provider(
    storage: Any, gateway: StreamGateway, at: datetime, started_at: datetime
) -> SimulatedBrokerProvider:
    def connector(_mode: Mode, _config: SnapshotConfig) -> IB:
        gateway.connected = True
        return cast("IB", gateway)

    return SimulatedBrokerProvider(
        storage,
        SnapshotConfig(
            modes=("paper",),
            executions_lookback=max(timedelta(days=1), at - started_at + timedelta(days=1)),
        ),
        connector=connector,
        clock=lambda: at,
        channel_prefix=f"simulation:{gateway.account.account_id}:",
        simulated=True,
    )
