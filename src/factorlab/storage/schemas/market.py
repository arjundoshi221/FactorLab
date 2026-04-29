"""market schema — candles, raw API responses."""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import col_created_at

SCHEMA = "market"
INST_FK = "ref.instruments.id"
CONTRACT_FK = "ref.contracts.id"

# ---------------------------------------------------------------------------
# market.candles_1min — TimescaleDB hypertable
# EQ candles: instrument_id set, contract_id NULL
# FUT candles: both instrument_id (underlying) and contract_id set
# ---------------------------------------------------------------------------
candles_1min = Table(
    "candles_1min",
    metadata,
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False),
    Column("contract_id", UUID(as_uuid=True), ForeignKey(CONTRACT_FK), nullable=True),
    Column("bar_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("volume", BigInteger),
    Column("oi", BigInteger, server_default="0", comment="Open interest (0 for EQ)"),
    Column("source", String(20), nullable=False, comment="upstox, ibkr"),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema=SCHEMA,
    comment="1-min OHLCV. TimescaleDB hypertable on bar_time, chunk=1day, compress after 7d.",
)

# Partial unique indexes: EQ vs FUT dedup
Index("ix_candles_1min_eq", candles_1min.c.instrument_id, candles_1min.c.bar_time,
      unique=True, postgresql_where=candles_1min.c.contract_id.is_(None))
Index("ix_candles_1min_fut", candles_1min.c.contract_id, candles_1min.c.bar_time,
      unique=True, postgresql_where=candles_1min.c.contract_id.isnot(None))
Index("ix_candles_1min_time", candles_1min.c.bar_time)

# ---------------------------------------------------------------------------
# market.candles_daily — TimescaleDB hypertable
# ---------------------------------------------------------------------------
candles_daily = Table(
    "candles_daily",
    metadata,
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False),
    Column("contract_id", UUID(as_uuid=True), ForeignKey(CONTRACT_FK), nullable=True),
    Column("trade_date", Date, nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("adj_close", Numeric(18, 6), comment="NULL for FUT"),
    Column("volume", BigInteger),
    Column("source", String(20), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema=SCHEMA,
    comment="Daily OHLCV. TimescaleDB hypertable on trade_date, chunk=1month.",
)

Index("ix_candles_daily_eq", candles_daily.c.instrument_id, candles_daily.c.trade_date,
      unique=True, postgresql_where=candles_daily.c.contract_id.is_(None))
Index("ix_candles_daily_fut", candles_daily.c.contract_id, candles_daily.c.trade_date,
      unique=True, postgresql_where=candles_daily.c.contract_id.isnot(None))
Index("ix_candles_daily_date", candles_daily.c.trade_date)

# ---------------------------------------------------------------------------
# market.raw_responses — immutable audit log of API responses
# ---------------------------------------------------------------------------
raw_responses = Table(
    "raw_responses",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("source", String(30), nullable=False, comment="upstox, eodhd, ibkr"),
    Column("endpoint", String(200), nullable=False, comment="API URL path"),
    Column("instrument_key", String(100), nullable=True, comment="For lookup"),
    Column("payload", JSONB, nullable=False),
    Column("row_count", Integer, nullable=True, comment="Candles in response"),
    Column("parsed_into", String(50), nullable=True, comment="Target table name"),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_raw_responses_fetched", raw_responses.c.fetched_at)
Index("ix_raw_responses_instrument", raw_responses.c.instrument_key)
