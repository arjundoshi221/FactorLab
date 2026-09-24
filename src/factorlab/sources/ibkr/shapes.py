"""Normalized IBKR broker facts for the ``broker.*`` mirror tables (schema-rehaul §9).

Each shape holds only what the broker reported plus the snapshot identity
(broker, account, mode, country). Columns owned by the storage layer are
added at write time by :class:`factorlab.storage.v2_broker.V2BrokerStorage`:

* identity — ``listing_id``/``security_id``/``contract_id``/``entity_id`` and
  ``resolution_confidence``, resolved from the conid via ``ref.identifier_aliases``;
* enrichment — ``metric_canonical`` (``ref.broker_metrics_map``),
  ``execution_method_id`` (``ref.execution_methods``), ``strategy_id``;
* provenance — ``source``, ``source_channel``, ``raw_id``, ``ingest_run_id``,
  ``as_of_time``, ``ingested_at``, ``version``.

Timestamps are UTC-aware ``datetime`` (asserted at construction); numbers are
``Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

Mode = Literal["paper", "live"]
BrokerCode = Literal["ibkr"]
SourceChannel = Literal["paper_gateway", "live_gateway"]
Side = Literal["BUY", "SELL", "SSHORT"]
ProductType = Literal[
    "common", "etf", "adr", "option", "future", "index", "forex", "bond", "fund", "other"
]

# IBKR secType -> canonical product_type (matches playground/explore/ibkr/07_dual_connect.py)
SEC_TYPE_TO_PRODUCT: dict[str, ProductType] = {
    "STK": "common",
    "ETF": "etf",
    "OPT": "option",
    "FUT": "future",
    "IND": "index",
    "CASH": "forex",
    "BOND": "bond",
    "FUND": "fund",
}


def source_channel_for(mode: Mode) -> SourceChannel:
    """Return the ``source_channel`` recorded for rows captured from ``mode``'s Gateway."""
    return f"{mode}_gateway"  # type: ignore[return-value]


def _require_utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a UTC-aware datetime, got naive: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Broker facts for one ``broker.positions_snapshot`` row (rehaul §9.1)."""

    snapshot_time: datetime
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str  # ISO-3166 alpha-2; 'US' for both IBKR accounts today
    product_type: ProductType
    vendor_id: str  # IBKR conid as string
    trading_symbol: str
    currency: str
    position: Decimal
    avg_cost: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl_ytd: Decimal | None
    market_value_usd: Decimal | None

    def __post_init__(self) -> None:
        _require_utc("snapshot_time", self.snapshot_time)


@dataclass(frozen=True, slots=True)
class AccountStateRow:
    """Broker facts for one ``broker.account_state_snapshot`` row (rehaul §9.2).

    Tall/long — one row per (metric, segment, currency) tuple. IBKR emits
    ~144 tags per account across segment/currency dimensions.
    """

    snapshot_time: datetime
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str
    metric: str
    segment: str  # '' | 'S' | 'C' | 'P'
    currency: str  # 'USD' | 'BASE' | 'NONE' | ...
    value_num: Decimal | None
    value_str: str | None

    def __post_init__(self) -> None:
        _require_utc("snapshot_time", self.snapshot_time)


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """Broker facts for one ``broker.executions`` row (rehaul §9.3). PK = ``exec_id``."""

    exec_id: str  # IBKR immutable exec id
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str
    order_id: int
    perm_id: int
    placed_by_client: int | None
    order_ref: str | None
    route_pref: str  # '' when the fill does not reveal the routing preference
    product_type: ProductType
    vendor_id: str
    trading_symbol: str
    currency: str
    exec_time: datetime
    side: Side
    quantity: Decimal
    price: Decimal
    exchange: str
    liquidity_flag: str
    commission: Decimal | None
    commission_ccy: str
    realized_pnl: Decimal | None

    def __post_init__(self) -> None:
        _require_utc("exec_time", self.exec_time)


@dataclass(frozen=True, slots=True)
class OpenOrderSnapshot:
    """Broker facts for one ``broker.open_orders_snapshot`` row (rehaul §9.4)."""

    snapshot_time: datetime
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str
    perm_id: int
    order_id: int
    placed_by_client: int | None
    order_ref: str | None
    product_type: ProductType
    vendor_id: str
    trading_symbol: str
    currency: str
    side: Side
    order_type: str
    time_in_force: str
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    limit_price: Decimal | None
    aux_price: Decimal | None
    status: str

    def __post_init__(self) -> None:
        _require_utc("snapshot_time", self.snapshot_time)
