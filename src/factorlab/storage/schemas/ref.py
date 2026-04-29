"""ref schema — reference dimensions: countries, currencies, FX, markets, exchanges, instruments, contracts."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import col_created_at, col_updated_at

SCHEMA = "ref"

# ---------------------------------------------------------------------------
# ref.countries — ISO 3166-1 country dimension
# ---------------------------------------------------------------------------
countries = Table(
    "countries",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(2), nullable=False, unique=True, comment="ISO 3166-1 alpha-2: IN, US, SG"),
    Column("name", String(100), nullable=False),
    Column("region", String(20), nullable=False, comment="asia, americas, europe"),
    Column("timezone", String(50), nullable=False, comment="Primary IANA timezone"),
    col_created_at(),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# ref.currencies — ISO 4217 currency dimension
# ---------------------------------------------------------------------------
currencies = Table(
    "currencies",
    metadata,
    Column("code", String(3), primary_key=True, comment="ISO 4217: INR, USD, SGD"),
    Column("name", String(50), nullable=False),
    Column("symbol", String(5), nullable=False, comment="Currency symbol"),
    Column("country_code", String(2), ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
    col_created_at(),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# ref.fx_pairs — full cross currency pairs
# ---------------------------------------------------------------------------
fx_pairs = Table(
    "fx_pairs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("base", String(3), ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
    Column("quote", String(3), ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
    Column("pair_code", String(7), nullable=False, unique=True, comment="INRUSD, USDINR, INRSGD"),
    Column("source", String(30), nullable=False, comment="ecb, rbi, upstox"),
    Column("active", Boolean, nullable=False, server_default="true"),
    col_created_at(),
    UniqueConstraint("base", "quote", name="uq_fx_pairs_base_quote"),
    CheckConstraint("base != quote", name="ck_fx_pairs_not_same"),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# ref.fx_rates_daily — daily FX rates
# ---------------------------------------------------------------------------
fx_rates_daily = Table(
    "fx_rates_daily",
    metadata,
    Column("pair_id", Integer, ForeignKey(f"{SCHEMA}.fx_pairs.id"), nullable=False, primary_key=True),
    Column("rate_date", Date, nullable=False, primary_key=True),
    Column("rate", Numeric(18, 8), nullable=False, comment="1 base = X quote"),
    Column("source", String(30), nullable=False),
    col_created_at(),
    schema=SCHEMA,
)

Index("ix_fx_rates_date", fx_rates_daily.c.rate_date)

# ---------------------------------------------------------------------------
# ref.markets — market dimension (IND, USA, EUR)
# ---------------------------------------------------------------------------
markets = Table(
    "markets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(10), nullable=False, unique=True, comment="IND, USA, EUR, GBR, SGP"),
    Column("name", String(100), nullable=False),
    Column("country_code", String(2), ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
    Column("currency_code", String(3), ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
    col_created_at(),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# ref.exchanges — exchange reference data
# ---------------------------------------------------------------------------
exchanges = Table(
    "exchanges",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(20), nullable=False, unique=True, comment="NSE, BSE, NYSE, NASDAQ"),
    Column("name", String(200), nullable=False),
    Column("market_code", String(10), ForeignKey(f"{SCHEMA}.markets.code"), nullable=False),
    Column("country_code", String(2), ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
    Column("currency_code", String(3), ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
    Column("timezone", String(50), nullable=False, comment="IANA tz: Asia/Kolkata, America/New_York"),
    Column("open_time", String(8), nullable=True, comment="HH:MM:SS local"),
    Column("close_time", String(8), nullable=True, comment="HH:MM:SS local"),
    Column("calendar_key", String(20), nullable=True, comment="exchange_calendars key: XBOM, XNYS"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# ref.instruments — one row per underlying tradeable entity
# RELIANCE = 1 row, AAPL = 1 row, NIFTY 50 = 1 row
# Derivatives contracts tracked in ref.contracts
# ---------------------------------------------------------------------------
instruments = Table(
    "instruments",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("exchange_id", Integer, ForeignKey(f"{SCHEMA}.exchanges.id"), nullable=False),
    Column("instrument_key", String(100), nullable=False, unique=True,
           comment="Vendor canonical: NSE_EQ|INE002A01018, AAPL.US"),
    Column("trading_symbol", String(50), nullable=False, comment="RELIANCE, AAPL"),
    Column("name", String(200), nullable=False),
    Column("isin", String(12), nullable=True, comment="ISIN (NULL for INDEX)"),
    Column("segment", String(20), nullable=False, comment="NSE_EQ, NSE_INDEX, US_EQ"),
    Column("instrument_type", String(10), nullable=False, comment="EQ, INDEX, ETF"),
    Column("asset_class", String(20), nullable=False, comment="equity, index, etf"),
    Column("country_code", String(2), ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
    Column("market_code", String(10), ForeignKey(f"{SCHEMA}.markets.code"), nullable=False),
    Column("currency_code", String(3), ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
    Column("lot_size", Integer, nullable=False, server_default="1"),
    Column("tick_size", Numeric(10, 2), nullable=True),
    Column("freeze_quantity", Numeric(12, 1), nullable=True),
    Column("exchange_token", String(20), nullable=True),
    Column("security_type", String(20), nullable=True, comment="NORMAL, etc."),
    Column("sector", String(100), nullable=True, comment="GICS sector or equivalent"),
    Column("status", String(20), nullable=False, server_default="active",
           comment="active, delisted, suspended"),
    Column("first_seen", Date, nullable=True),
    Column("last_seen", Date, nullable=True),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

Index("ix_instruments_symbol", instruments.c.trading_symbol)
Index("ix_instruments_segment", instruments.c.segment)
Index("ix_instruments_isin", instruments.c.isin)
Index("ix_instruments_country", instruments.c.country_code)
Index("ix_instruments_market", instruments.c.market_code)
Index("ix_instruments_status", instruments.c.status)

# ---------------------------------------------------------------------------
# ref.contracts — one row per derivatives contract
# RELIANCE FUT APR 26 = 1 row, RELIANCE FUT MAY 26 = 1 row
# FK back to the underlying instrument
# ---------------------------------------------------------------------------
contracts = Table(
    "contracts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.instruments.id"), nullable=False,
           comment="The underlying: RELIANCE, NIFTY"),
    Column("exchange_id", Integer, ForeignKey(f"{SCHEMA}.exchanges.id"), nullable=False),
    Column("contract_key", String(100), nullable=False, unique=True, comment="Vendor key: NSE_FO|67003"),
    Column("trading_symbol", String(80), nullable=False, comment="RELIANCE FUT 26 APR 26"),
    Column("contract_type", String(10), nullable=False, comment="FUT, CE, PE"),
    Column("segment", String(20), nullable=False, comment="NSE_FO, BSE_FO"),
    Column("expiry", Date, nullable=False),
    Column("strike_price", Numeric(18, 2), nullable=True, comment="0 for FUT, strike for OPT"),
    Column("lot_size", Integer, nullable=False),
    Column("tick_size", Numeric(10, 2), nullable=True),
    Column("freeze_quantity", Numeric(12, 1), nullable=True),
    Column("exchange_token", String(20), nullable=True),
    Column("weekly", Boolean, nullable=False, server_default="false"),
    Column("status", String(20), nullable=False, server_default="active", comment="active, expired"),
    Column("first_seen", Date, nullable=True),
    Column("last_seen", Date, nullable=True),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

Index("ix_contracts_instrument", contracts.c.instrument_id)
Index("ix_contracts_expiry", contracts.c.expiry)
Index("ix_contracts_segment", contracts.c.segment)
Index("ix_contracts_status", contracts.c.status)
Index("ix_contracts_nearest", contracts.c.instrument_id, contracts.c.expiry, contracts.c.contract_type)

# ---------------------------------------------------------------------------
# ref.instrument_daily — SCD for mutable instrument fields
# ---------------------------------------------------------------------------
instrument_daily = Table(
    "instrument_daily",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.instruments.id"), nullable=False),
    Column("snapshot_date", Date, nullable=False),
    Column("lot_size", Integer, nullable=True),
    Column("freeze_quantity", Numeric(12, 1), nullable=True),
    Column("tick_size", Numeric(10, 2), nullable=True),
    Column("source", String(30), nullable=False),
    col_created_at(),
    UniqueConstraint("instrument_id", "snapshot_date", name="uq_instrument_daily_id_date"),
    schema=SCHEMA,
)
