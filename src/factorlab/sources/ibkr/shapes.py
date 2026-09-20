"""Row shapes for the ``broker.*`` mirror tables (schema-rehaul §9).

Field names and types map 1:1 to the CREATE TABLE columns. Timestamps are
UTC-aware ``datetime`` (asserted at construction); numbers are ``Decimal``;
canonical FactorLab IDs (``listing_id``, ``security_id``, ``entity_id``,
``contract_id``) are nullable ``UUID`` — populated later by the identifier
resolver (Wave 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

Mode = Literal["paper", "live"]
BrokerCode = Literal["ibkr"]
Source = Literal["ibkr"]
SourceChannel = Literal["paper_gateway", "live_gateway"]
ResolutionConfidence = Literal[
    "exact", "high", "medium", "low", "unresolved", "manual_override"
]
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


def _require_utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a UTC-aware datetime, got naive: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Row for ``broker.positions_snapshot`` (rehaul §9.1)."""

    snapshot_time: datetime
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str  # ISO-3166 alpha-2; 'US' for both IBKR accounts today
    # canonical FactorLab IDs — nullable until resolver populates
    listing_id: UUID | None
    security_id: UUID | None
    contract_id: UUID | None
    entity_id: UUID | None
    product_type: ProductType
    # vendor-native fallback
    vendor_id: str  # IBKR conid as string
    trading_symbol: str
    currency: str
    # position
    position: Decimal
    avg_cost: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl_ytd: Decimal | None
    market_value_usd: Decimal | None
    # resolution audit
    resolution_confidence: ResolutionConfidence
    # provenance + PIT
    source: Source
    source_channel: SourceChannel
    as_of_time: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_utc("snapshot_time", self.snapshot_time)
        _require_utc("as_of_time", self.as_of_time)
        _require_utc("ingested_at", self.ingested_at)


@dataclass(frozen=True, slots=True)
class AccountStateRow:
    """Row for ``broker.account_state_snapshot`` (rehaul §9.2).

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
    source: Source
    source_channel: SourceChannel
    as_of_time: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_utc("snapshot_time", self.snapshot_time)
        _require_utc("as_of_time", self.as_of_time)
        _require_utc("ingested_at", self.ingested_at)


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """Row for ``broker.executions`` (rehaul §9.3). PK = ``exec_id``."""

    exec_id: str  # IBKR immutable exec id
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str
    order_id: int
    perm_id: int
    placed_by_client: int | None
    listing_id: UUID | None
    security_id: UUID | None
    contract_id: UUID | None
    entity_id: UUID | None
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
    resolution_confidence: ResolutionConfidence
    source: Source
    source_channel: SourceChannel
    as_of_time: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_utc("exec_time", self.exec_time)
        _require_utc("as_of_time", self.as_of_time)
        _require_utc("ingested_at", self.ingested_at)


@dataclass(frozen=True, slots=True)
class OpenOrderSnapshot:
    """Row for ``broker.open_orders_snapshot`` (rehaul §9.4)."""

    snapshot_time: datetime
    broker_code: BrokerCode
    account_id: str
    account_mode: Mode
    country_code: str
    perm_id: int
    order_id: int
    placed_by_client: int | None
    listing_id: UUID | None
    security_id: UUID | None
    contract_id: UUID | None
    entity_id: UUID | None
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
    resolution_confidence: ResolutionConfidence
    source: Source
    source_channel: SourceChannel
    as_of_time: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_utc("snapshot_time", self.snapshot_time)
        _require_utc("as_of_time", self.as_of_time)
        _require_utc("ingested_at", self.ingested_at)
