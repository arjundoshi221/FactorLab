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
    # NOTE: country_code + currency_code dropped in mig 026 — derive via JOIN to markets.
    Column("market_code", String(10), ForeignKey(f"{SCHEMA}.markets.code"), nullable=False),
    Column("lot_size", Integer, nullable=False, server_default="1"),
    Column("tick_size", Numeric(10, 2), nullable=True),
    Column("freeze_quantity", Numeric(12, 1), nullable=True),
    Column("exchange_token", String(20), nullable=True),
    Column("security_type", String(20), nullable=True, comment="NORMAL, etc."),
    # GICS classification — populated by US fundamentals backfill (Phase G or later)
    Column("gics_sector_id", Integer, ForeignKey(f"{SCHEMA}.gics_sectors.id"), nullable=True),
    Column("gics_industry_id", Integer, ForeignKey(f"{SCHEMA}.gics_industries.id"), nullable=True),
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
Index("ix_instruments_market", instruments.c.market_code)
Index("ix_instruments_status", instruments.c.status)
Index("ix_instruments_gics_sector_id", instruments.c.gics_sector_id,
      postgresql_where=instruments.c.gics_sector_id.isnot(None))
Index("ix_instruments_gics_industry_id", instruments.c.gics_industry_id,
      postgresql_where=instruments.c.gics_industry_id.isnot(None))

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
# ref.vendors — data providers / source organizations (parent dim)
# ---------------------------------------------------------------------------
vendors = Table(
    "vendors",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(40), nullable=False, unique=True,
           comment="Short canonical code: upstox, house_clerk, eodhd, ..."),
    Column("name", String(200), nullable=False),
    Column("vendor_type", String(15), nullable=False,
           comment="api / scrape / file / mirror / official"),
    Column("homepage_url", String(500), nullable=True),
    Column("notes", String(2000), nullable=True),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    col_created_at(),
    CheckConstraint(
        "vendor_type IN ('api','scrape','file','mirror','official')",
        name="vendor_type",
    ),
    schema=SCHEMA,
    comment="Data providers / source organizations. Parent of data_endpoints.",
)
Index("ix_vendors_code", vendors.c.code)


# ---------------------------------------------------------------------------
# ref.data_endpoints — specific feeds / URL patterns (child of vendors)
# ---------------------------------------------------------------------------
data_endpoints = Table(
    "data_endpoints",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vendor_id", Integer, ForeignKey(f"{SCHEMA}.vendors.id"), nullable=False),
    Column("code", String(60), nullable=False, unique=True,
           comment="Endpoint canonical code: house_clerk_ptr, senate_efd_ptr, ..."),
    Column("name", String(200), nullable=False),
    Column("endpoint_type", String(15), nullable=False,
           comment="rest / graphql / scrape / file / yaml / dump"),
    Column("url_pattern", String(500), nullable=True,
           comment="Templated URL with {placeholders} where applicable"),
    Column("notes", String(2000), nullable=True),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    col_created_at(),
    CheckConstraint(
        "endpoint_type IN ('rest','graphql','scrape','file','yaml','dump')",
        name="endpoint_type",
    ),
    schema=SCHEMA,
    comment="Specific feeds / URL patterns. Each fact-table row's endpoint_id "
            "FKs here; the vendor is reached via the FK chain.",
)
Index("ix_endpoints_code", data_endpoints.c.code)
Index("ix_endpoints_vendor", data_endpoints.c.vendor_id)


# ---------------------------------------------------------------------------
# ref.frequencies — bar-frequency dim (FK target for fact tables' freq_id)
# ---------------------------------------------------------------------------
frequencies = Table(
    "frequencies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(8), nullable=False, unique=True,
           comment="Canonical short code: 1m, 5m, 15m, 1h, 1d, 1w, 1M"),
    Column("name", String(40), nullable=False),
    Column("seconds", Integer, nullable=False,
           comment="Approximate bar duration in seconds (1d=86400, 1M=2592000)"),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    col_created_at(),
    CheckConstraint("seconds > 0", name="seconds_positive"),
    schema=SCHEMA,
    comment="Bar-frequency dim. FK target for fact tables' freq_id.",
)
Index("ix_frequencies_code", frequencies.c.code)


# ---------------------------------------------------------------------------
# ref.gics_sectors — 11 canonical GICS sectors
# ---------------------------------------------------------------------------
gics_sectors = Table(
    "gics_sectors",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(8), nullable=False, unique=True,
           comment="GICS 2-digit sector code"),
    Column("name", String(80), nullable=False),
    col_created_at(),
    schema=SCHEMA,
    comment="GICS top-level sectors (11). FK target for instruments.gics_sector_id.",
)
Index("ix_gics_sectors_code", gics_sectors.c.code)


# ---------------------------------------------------------------------------
# ref.gics_industries — GICS industry groups (24), 2-level collapsed
# ---------------------------------------------------------------------------
gics_industries = Table(
    "gics_industries",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(8), nullable=False, unique=True,
           comment="GICS 4-digit industry-group code"),
    Column("name", String(120), nullable=False),
    Column("sector_id", Integer, ForeignKey(f"{SCHEMA}.gics_sectors.id"),
           nullable=False),
    col_created_at(),
    schema=SCHEMA,
    comment="GICS industry groups (24). FK target for instruments.gics_industry_id.",
)
Index("ix_gics_industries_code", gics_industries.c.code)
Index("ix_gics_industries_sector", gics_industries.c.sector_id)


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
