"""market_in schema — India equity / index / F&O OHLCV bars (Upstox + IBKR India).

Per-country market schema. US prices land in `market_us`; future EU/APAC in
sibling schemas. Reference dims (instruments, contracts, exchanges) shared in
`ref`. Audit goes to `audit.raw_archive`.

Three split-by-asset-class fact tables, all TimescaleDB hypertables on bar_time:

  fact_equity   stocks + ETFs       instrument_id FK; adj_close + volume
  fact_index    indices             instrument_id FK; no adj_close, no oi
  fact_futures  derivatives         contract_id NOT NULL + underlying instrument_id; oi

All three keyed on `freq_id` FK to ref.frequencies (single dim controls
1m / 5m / 1d / etc. — adding a new freq is a row INSERT, not a schema change).
Every row carries `endpoint_id` FK to ref.data_endpoints (which feed produced it).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from factorlab.storage.db import metadata

SCHEMA = "market_in"
INST_FK = "ref.instruments.id"
CONTRACT_FK = "ref.contracts.id"
FREQ_FK = "ref.frequencies.id"
ENDPOINT_FK = "ref.data_endpoints.id"


# ---------------------------------------------------------------------------
# market_in.fact_equity — stocks + ETFs (TimescaleDB hypertable)
# ---------------------------------------------------------------------------
fact_equity = Table(
    "fact_equity",
    metadata,
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False),
    Column("freq_id", Integer, ForeignKey(FREQ_FK), nullable=False),
    Column("bar_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("adj_close", Numeric(18, 6),
           comment="Split/dividend-adjusted close. NULL for intraday bars."),
    Column("volume", BigInteger),
    Column("session", String(8), nullable=False, server_default="regular",
           comment="pre / regular / post (US extended hours; IN always 'regular')."),
    Column("endpoint_id", Integer, ForeignKey(ENDPOINT_FK), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False,
           server_default=func.now()),
    CheckConstraint("session IN ('pre', 'regular', 'post')", name="session"),
    schema=SCHEMA,
    comment="Stocks + ETFs OHLCV bars (any freq via freq_id). Hypertable, chunk=7d.",
)
Index("ix_market_in_fact_equity_uniq", fact_equity.c.instrument_id,
      fact_equity.c.freq_id, fact_equity.c.bar_time, unique=True)
Index("ix_market_in_fact_equity_freq_time", fact_equity.c.freq_id, fact_equity.c.bar_time)
Index("ix_market_in_fact_equity_endpoint", fact_equity.c.endpoint_id)


# ---------------------------------------------------------------------------
# market_in.fact_index — indices (TimescaleDB hypertable)
# ---------------------------------------------------------------------------
fact_index = Table(
    "fact_index",
    metadata,
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False),
    Column("freq_id", Integer, ForeignKey(FREQ_FK), nullable=False),
    Column("bar_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("volume", BigInteger,
           comment="Index volume — often NULL; some vendors report broad-market totals."),
    Column("session", String(8), nullable=False, server_default="regular"),
    Column("endpoint_id", Integer, ForeignKey(ENDPOINT_FK), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False,
           server_default=func.now()),
    CheckConstraint("session IN ('pre', 'regular', 'post')", name="session"),
    schema=SCHEMA,
    comment="Index OHLC bars (NIFTY, S&P 500, etc.). Hypertable, chunk=7d.",
)
Index("ix_market_in_fact_index_uniq", fact_index.c.instrument_id,
      fact_index.c.freq_id, fact_index.c.bar_time, unique=True)
Index("ix_market_in_fact_index_freq_time", fact_index.c.freq_id, fact_index.c.bar_time)
Index("ix_market_in_fact_index_endpoint", fact_index.c.endpoint_id)


# ---------------------------------------------------------------------------
# market_in.fact_futures — derivatives contracts (TimescaleDB hypertable)
# ---------------------------------------------------------------------------
fact_futures = Table(
    "fact_futures",
    metadata,
    Column("contract_id", UUID(as_uuid=True), ForeignKey(CONTRACT_FK), nullable=False,
           comment="The specific futures/options contract."),
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False,
           comment="The underlying instrument (RELIANCE, NIFTY)."),
    Column("freq_id", Integer, ForeignKey(FREQ_FK), nullable=False),
    Column("bar_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("volume", BigInteger),
    Column("oi", BigInteger, nullable=False, server_default="0",
           comment="Open interest at bar close."),
    Column("session", String(8), nullable=False, server_default="regular"),
    Column("endpoint_id", Integer, ForeignKey(ENDPOINT_FK), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False,
           server_default=func.now()),
    CheckConstraint("session IN ('pre', 'regular', 'post')", name="session"),
    schema=SCHEMA,
    comment="Derivatives contract OHLCV+OI bars. Hypertable, chunk=7d.",
)
Index("ix_market_in_fact_futures_uniq", fact_futures.c.contract_id,
      fact_futures.c.freq_id, fact_futures.c.bar_time, unique=True)
Index("ix_market_in_fact_futures_underlying_time",
      fact_futures.c.instrument_id, fact_futures.c.bar_time)
Index("ix_market_in_fact_futures_freq_time",
      fact_futures.c.freq_id, fact_futures.c.bar_time)
Index("ix_market_in_fact_futures_endpoint", fact_futures.c.endpoint_id)
