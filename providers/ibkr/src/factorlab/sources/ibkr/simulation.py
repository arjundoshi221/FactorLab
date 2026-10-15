"""Deterministic broker responses, using the normal capture and normalization path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, cast

from factorlab.ingest.profiles import ProfileAccount
from factorlab.sources.ibkr.provider import IBKRBrokerProvider, SnapshotConfig
from factorlab.sources.ibkr.shapes import Mode

if TYPE_CHECKING:
    from ib_async import IB

# Fixed execution identity/time ensures reruns collapse rather than inventing new fills.
EXECUTION_EPOCH = datetime(2026, 10, 1, 14, 30, tzinfo=UTC)
STEPS = ("initial", "order", "filled", "closed")


class SimulatedGateway:
    _factorlab_mode = "paper"
    RequestTimeout = 15.0

    def __init__(self, account: ProfileAccount, step: str, quantity: int) -> None:
        if not account.simulated or step not in STEPS:
            raise ValueError("simulation requires a synthetic paper account and a valid step")
        self.account = account
        self.step = step
        self.quantity = quantity
        self.connected = True
        self.contract = {
            "conId": 265598,
            "symbol": "AAPL",
            "secType": "STK",
            "currency": account.base_currency,
            "exchange": "SMART",
        }

    def managedAccounts(self) -> list[str]:
        return [self.account.account_id]

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False

    def portfolio(self) -> list[dict[str, Any]]:
        if self.step != "filled":
            return []
        return [
            {
                "account": self.account.account_id,
                "contract": self.contract,
                "position": self.quantity,
                "averageCost": 200,
                "marketPrice": 202,
                "marketValue": 202 * self.quantity,
                "unrealizedPNL": 2 * self.quantity,
                "realizedPNL": 0,
            }
        ]

    def accountValues(self) -> list[dict[str, Any]]:
        profit = 2 * self.quantity if self.step in ("filled", "closed") else 0
        cash = 100000 - 200 * self.quantity if self.step == "filled" else 100000 + profit
        return [
            {
                "account": self.account.account_id,
                "tag": tag,
                "value": str(value),
                "currency": self.account.base_currency,
            }
            for tag, value in (
                ("NetLiquidation", 100000 + profit),
                ("TotalCashValue", cash),
                ("BuyingPower", cash),
            )
        ]

    def reqAllOpenOrders(self) -> list[SimpleNamespace]:
        if self.step != "order":
            return []
        return [
            SimpleNamespace(
                contract=self.contract,
                order={
                    "account": self.account.account_id,
                    "permId": 1,
                    "orderId": 1,
                    "clientId": 0,
                    "action": "BUY",
                    "orderType": "LMT",
                    "tif": "DAY",
                    "totalQuantity": self.quantity,
                    "lmtPrice": 200,
                },
                orderStatus={"status": "Submitted", "filled": 0, "remaining": self.quantity},
            )
        ]

    def reqExecutions(self, _filter: Any = None) -> list[SimpleNamespace]:
        if self.step not in ("filled", "closed"):
            return []
        sides = ("BOT", "SLD") if self.step == "closed" else ("BOT",)
        return [
            SimpleNamespace(
                contract=self.contract,
                execution={
                    "execId": f"{self.account.account_id}:basic:{side}",
                    "acctNumber": self.account.account_id,
                    "orderId": index + 1,
                    "permId": index + 1,
                    "clientId": 0,
                    "side": side,
                    "shares": self.quantity,
                    "price": 200 + 2 * index,
                    "time": EXECUTION_EPOCH + timedelta(minutes=index),
                    "exchange": "SIMULATED",
                },
                commissionReport={
                    "commission": 0,
                    "currency": self.account.base_currency,
                    "realizedPNL": 2 * self.quantity if index else 0,
                },
                time=EXECUTION_EPOCH + timedelta(minutes=index),
            )
            for index, side in enumerate(sides)
        ]


class SimulatedBrokerProvider(IBKRBrokerProvider):
    source: ClassVar[str] = "ibkr_simulation"
    pipeline: ClassVar[str] = "ibkr_broker_simulation"


def simulated_provider(
    storage: Any, account: ProfileAccount, step: str, *, at: datetime, quantity: int = 10
) -> SimulatedBrokerProvider:
    def connector(_mode: Mode, _config: SnapshotConfig) -> IB:
        return cast("IB", SimulatedGateway(account, step, quantity))

    return SimulatedBrokerProvider(
        storage,
        SnapshotConfig(
            modes=("paper",),
            executions_lookback=max(timedelta(days=1), at - EXECUTION_EPOCH + timedelta(days=1)),
        ),
        connector=connector,
        clock=lambda: at,
        channel_prefix=f"simulation:{account.account_id}:",
        simulated=True,
    )
