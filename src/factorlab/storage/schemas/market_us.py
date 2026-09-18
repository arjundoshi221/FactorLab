"""market_us schema — US equity / index / F&O OHLCV bars (Schwab + EODHD + IBKR US).

Per-country market schema mirroring `market_in`. Same three split-by-asset-class
fact tables: `fact_equity`, `fact_index`, `fact_futures`. Same column shapes,
same indexes, same hypertable chunk policy. Only the schema namespace differs
so US data and India data never accidentally mix in a single SELECT without
the FROM clause making it explicit.
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

SCHEMA = "market_us"
INST_FK = "ref.instruments.id"
CONTRACT_FK = "ref.contracts.id"
FREQ_FK = "ref.frequencies.id"
ENDPOINT_FK = "ref.data_endpoints.id"


# ---------------------------------------------------------------------------
# market_us.fact_equity — stocks + ETFs (TimescaleDB hypertable)
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
           comment="pre / regular / post (US extended hours)."),
    Column("endpoint_id", Integer, ForeignKey(ENDPOINT_FK), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False,
           server_default=func.now()),
    CheckConstraint("session IN ('pre', 'regular', 'post')", name="session"),
    schema=SCHEMA,
    comment="Stocks + ETFs OHLCV bars (any freq via freq_id). Hypertable, chunk=7d.",
)
Index("ix_market_us_fact_equity_uniq", fact_equity.c.instrument_id,
      fact_equity.c.freq_id, fact_equity.c.bar_time, unique=True)
Index("ix_market_us_fact_equity_freq_time", fact_equity.c.freq_id, fact_equity.c.bar_time)
Index("ix_market_us_fact_equity_endpoint", fact_equity.c.endpoint_id)


# ---------------------------------------------------------------------------
# market_us.fact_index — indices (TimescaleDB hypertable)
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
    comment="Index OHLC bars (S&P 500, NASDAQ Composite, etc.). Hypertable, chunk=7d.",
)
Index("ix_market_us_fact_index_uniq", fact_index.c.instrument_id,
      fact_index.c.freq_id, fact_index.c.bar_time, unique=True)
Index("ix_market_us_fact_index_freq_time", fact_index.c.freq_id, fact_index.c.bar_time)
Index("ix_market_us_fact_index_endpoint", fact_index.c.endpoint_id)


# ---------------------------------------------------------------------------
# market_us.fact_futures — derivatives contracts (TimescaleDB hypertable)
# ---------------------------------------------------------------------------
fact_futures = Table(
    "fact_futures",
    metadata,
    Column("contract_id", UUID(as_uuid=True), ForeignKey(CONTRACT_FK), nullable=False,
           comment="The specific futures/options contract."),
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False,
           comment="The underlying instrument (SPY, ES, etc.)."),
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
Index("ix_market_us_fact_futures_uniq", fact_futures.c.contract_id,
      fact_futures.c.freq_id, fact_futures.c.bar_time, unique=True)
Index("ix_market_us_fact_futures_underlying_time",
      fact_futures.c.instrument_id, fact_futures.c.bar_time)
Index("ix_market_us_fact_futures_freq_time",
      fact_futures.c.freq_id, fact_futures.c.bar_time)
Index("ix_market_us_fact_futures_endpoint", fact_futures.c.endpoint_id)
