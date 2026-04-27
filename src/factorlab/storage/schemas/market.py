"""market schema — prices, fundamentals, corporate actions, raw archives, IBKR mirrors."""

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
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import (
    col_as_of_time,
    col_created_at,
    col_currency,
    col_market,
    col_source,
    col_updated_at,
)

SCHEMA = "market"
SEC_FK = "ref.securities.id"

# ---------------------------------------------------------------------------
# market.price_bars_daily — OHLCV, partitioned by trade_date (yearly)
# ---------------------------------------------------------------------------
price_bars_daily = Table(
    "price_bars_daily",
    metadata,
    Column("id", UUID(as_uuid=True), server_default=text("gen_random_uuid()")),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("adj_close", Numeric(18, 6)),
    Column("volume", BigInteger),
    col_market(),
    col_currency(),
    col_source(),
    col_as_of_time(),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
    comment="Daily OHLCV. TimescaleDB hypertable on trade_date.",
)

Index("ix_price_daily_security_date", price_bars_daily.c.security_id, price_bars_daily.c.trade_date.desc(), unique=True)
Index("ix_price_daily_date", price_bars_daily.c.trade_date)
Index("ix_price_daily_as_of", price_bars_daily.c.security_id, price_bars_daily.c.as_of_time.desc())

# ---------------------------------------------------------------------------
# market.price_bars_minute — intraday bars (India, IBKR)
# ---------------------------------------------------------------------------
price_bars_minute = Table(
    "price_bars_minute",
    metadata,
    Column("id", UUID(as_uuid=True), server_default=text("gen_random_uuid()")),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=False),
    Column("instrument_key", String(100), nullable=True, comment="Vendor canonical key e.g. NSE_FO|67003"),
    Column("segment", String(20), nullable=True, comment="NSE_EQ, NSE_FO — differentiates equity vs futures bars"),
    Column("bar_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric(18, 6)),
    Column("high", Numeric(18, 6)),
    Column("low", Numeric(18, 6)),
    Column("close", Numeric(18, 6)),
    Column("volume", BigInteger),
    Column("open_interest", BigInteger, nullable=True, comment="Non-zero for futures/options"),
    col_market(),
    col_currency(),
    col_source(),
    col_as_of_time(),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
    comment="Minute OHLCV. TimescaleDB hypertable on bar_time, compressed after 7 days.",
)

Index("ix_price_minute_security_time", price_bars_minute.c.security_id, price_bars_minute.c.bar_time.desc(), unique=True)
Index("ix_price_minute_time", price_bars_minute.c.bar_time)
Index("ix_price_minute_segment", price_bars_minute.c.segment)
Index("ix_price_minute_instrument", price_bars_minute.c.instrument_key)

# ---------------------------------------------------------------------------
# market.fundamentals — point-in-time (restatements = multiple as_of rows)
# ---------------------------------------------------------------------------
fundamentals = Table(
    "fundamentals",
    metadata,
    Column("id", UUID(as_uuid=True), server_default=text("gen_random_uuid()")),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=False),
    Column("period_end", Date, nullable=False, comment="Quarter/year being reported"),
    Column("filing_date", Date, nullable=False, comment="Knowledge date — when filed/publicly knowable. Backtest filter: filing_date <= as_of"),
    Column("metric", String(80), nullable=False, comment="revenue, net_income, total_assets, ..."),
    Column("value", Numeric(28, 6)),
    col_market(),
    col_currency(),
    col_source(),
    col_as_of_time(),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
    comment="Point-in-time fundamentals. Multiple as_of rows capture restatements.",
)

Index("ix_fundamentals_pit", fundamentals.c.security_id, fundamentals.c.period_end, fundamentals.c.metric, fundamentals.c.as_of_time.desc(), unique=True)
Index("ix_fundamentals_filing", fundamentals.c.filing_date)

# ---------------------------------------------------------------------------
# market.corporate_actions — splits, dividends, mergers
# ---------------------------------------------------------------------------
corporate_actions = Table(
    "corporate_actions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=False),
    Column("action_type", String(30), nullable=False, comment="split, dividend, merger, spinoff"),
    Column("ex_date", Date, nullable=False),
    Column("record_date", Date, nullable=True),
    Column("pay_date", Date, nullable=True),
    Column("factor", Numeric(18, 10), nullable=True, comment="Split ratio or dividend amount"),
    col_market(),
    col_currency(),
    col_source(),
    col_as_of_time(),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

Index("ix_corpact_security_date", corporate_actions.c.security_id, corporate_actions.c.ex_date.desc())

# ---------------------------------------------------------------------------
# Raw archive tables — one per vendor, never deleted
# ---------------------------------------------------------------------------
raw_eodhd = Table(
    "raw_eodhd",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("endpoint", String(200), nullable=False, comment="/api/eod/AAPL.US, /api/fundamentals/..."),
    Column("source_url", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("parsed_into", String(100), nullable=True, comment="Target fact table"),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_raw_eodhd_fetched", raw_eodhd.c.fetched_at)

raw_upstox = Table(
    "raw_upstox",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("endpoint", String(200), nullable=False),
    Column("instrument_key", String(100), nullable=False),
    Column("source_url", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("parsed_into", String(100), nullable=True),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_raw_upstox_fetched", raw_upstox.c.fetched_at)
Index("ix_raw_upstox_instrument", raw_upstox.c.instrument_key)

raw_ibkr = Table(
    "raw_ibkr",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("request_type", String(80), nullable=False, comment="historical_data, positions, executions, ..."),
    Column("conid", Integer, nullable=True, comment="IBKR contract ID"),
    Column("payload", JSONB, nullable=False),
    Column("parsed_into", String(100), nullable=True),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_raw_ibkr_fetched", raw_ibkr.c.fetched_at)

# ---------------------------------------------------------------------------
# IBKR portfolio mirrors — broker is master, Postgres is audit log
# ---------------------------------------------------------------------------
ibkr_positions_snapshot = Table(
    "ibkr_positions_snapshot",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("snapshot_at", DateTime(timezone=True), nullable=False),
    Column("account", String(20), nullable=False),
    Column("account_mode", String(10), nullable=False, comment="paper or live"),
    Column("conid", Integer, nullable=False),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=True),
    Column("position", Numeric(18, 4), nullable=False),
    Column("avg_cost", Numeric(18, 6)),
    Column("market_value", Numeric(18, 4)),
    Column("unrealized_pnl", Numeric(18, 4)),
    Column("realized_pnl_today", Numeric(18, 4)),
    col_market(),
    col_currency(),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_ibkr_pos_snapshot", ibkr_positions_snapshot.c.snapshot_at, ibkr_positions_snapshot.c.account)

ibkr_executions = Table(
    "ibkr_executions",
    metadata,
    Column("exec_id", String(80), primary_key=True, comment="IBKR exec ID — immutable"),
    Column("account", String(20), nullable=False),
    Column("account_mode", String(10), nullable=False),
    Column("order_id", Integer, nullable=False),
    Column("perm_id", Integer, nullable=False, comment="Durable across restarts — join on this"),
    Column("conid", Integer, nullable=False),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=True),
    Column("side", String(10), nullable=False),
    Column("quantity", Numeric(18, 4), nullable=False),
    Column("price", Numeric(18, 6), nullable=False),
    Column("exchange", String(20), nullable=False),
    Column("exec_time", DateTime(timezone=True), nullable=False),
    Column("commission", Numeric(12, 4)),
    Column("realized_pnl", Numeric(18, 4)),
    col_market(),
    col_currency(),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_ibkr_exec_time", ibkr_executions.c.exec_time)
Index("ix_ibkr_exec_perm", ibkr_executions.c.perm_id)

ibkr_account_values = Table(
    "ibkr_account_values_snapshot",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("snapshot_at", DateTime(timezone=True), nullable=False),
    Column("account", String(20), nullable=False),
    Column("account_mode", String(10), nullable=False),
    Column("key", String(80), nullable=False, comment="NetLiquidation, CashBalance, ..."),
    Column("value", String, nullable=False),
    col_currency(),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_ibkr_acct_snapshot", ibkr_account_values.c.snapshot_at, ibkr_account_values.c.account)

# ---------------------------------------------------------------------------
# market.adjustment_factors — split/dividend factor history
# Reconstruct what adjusted prices looked like on any past date.
# Vendors silently rewrite adj_close retroactively; this table captures the factor
# at each ex_date so we can re-derive adjusted prices point-in-time.
# ---------------------------------------------------------------------------
adjustment_factors = Table(
    "adjustment_factors",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=False),
    Column("ex_date", Date, nullable=False, comment="Date the action takes effect"),
    Column("factor_type", String(20), nullable=False, comment="split, dividend, rights, spinoff"),
    Column("factor", Numeric(18, 10), nullable=False, comment="Ratio: 2.0 for 2-for-1 split, 0.98 for 2% dividend"),
    Column("cumulative_factor", Numeric(18, 10), nullable=False, comment="Running product of all factors up to this date"),
    col_market(),
    col_currency(),
    col_source(),
    col_as_of_time(),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
    comment="Adjustment factor history for reconstructing point-in-time adjusted prices.",
)

Index("ix_adjfactor_security_date", adjustment_factors.c.security_id, adjustment_factors.c.ex_date.desc())
Index("ix_adjfactor_as_of", adjustment_factors.c.security_id, adjustment_factors.c.as_of_time.desc())
