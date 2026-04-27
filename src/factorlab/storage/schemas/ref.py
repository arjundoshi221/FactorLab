"""ref schema — reference data: securities, aliases, exchanges, calendars."""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import col_created_at, col_updated_at

SCHEMA = "ref"

# ---------------------------------------------------------------------------
# ref.securities — canonical security identity (stable UUID per security)
# ---------------------------------------------------------------------------
securities = Table(
    "securities",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", String, nullable=False),
    Column("country", String(2), nullable=False, comment="ISO 3166-1 alpha-2"),
    Column("asset_class", String(20), nullable=False, comment="equity, future, index, etf, option, commodity"),
    Column("sector", String(100), nullable=True, comment="GICS sector or equivalent"),
    Column("status", String(20), nullable=False, server_default="active", comment="active, delisted, merged, suspended"),
    Column("segment", String(20), nullable=True, comment="NSE_EQ, NSE_FO, BSE_EQ, etc."),
    Column("underlying_symbol", String(50), nullable=True, comment="For derivatives: underlying equity symbol (e.g. RELIANCE)"),
    Column("expiry", Date, nullable=True, comment="Contract expiry date (NULL for equities)"),
    Column("lot_size", Integer, nullable=True, comment="F&O lot size (NULL for equities)"),
    Column("market", String(10), nullable=False, comment="us, in, eu, gb, ..."),
    Column("currency", String(3), nullable=False, comment="Primary listing currency (ISO 4217)"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

Index("ix_securities_market", securities.c.market)
Index("ix_securities_asset_class", securities.c.asset_class)
Index("ix_securities_status", securities.c.status)
Index("ix_securities_country", securities.c.country)
Index("ix_securities_segment", securities.c.segment)
Index("ix_securities_underlying", securities.c.underlying_symbol)

# ---------------------------------------------------------------------------
# ref.security_aliases — vendor-specific ID mapping
# ---------------------------------------------------------------------------
security_aliases = Table(
    "security_aliases",
    metadata,
    Column("security_id", UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.securities.id"), nullable=False),
    Column("vendor", String(30), nullable=False, comment="eodhd, upstox, ibkr, reddit, senate, arxiv"),
    Column("vendor_id", String(100), nullable=False, comment="Vendor-native identifier"),
    Column("valid_from", Date, nullable=False),
    Column("valid_to", Date, nullable=True, comment="null = current"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

Index("ix_security_aliases_lookup", security_aliases.c.vendor, security_aliases.c.vendor_id)
Index("ix_security_aliases_security", security_aliases.c.security_id)

# ---------------------------------------------------------------------------
# ref.exchanges — exchange reference data
# ---------------------------------------------------------------------------
exchanges = Table(
    "exchanges",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("code", String(20), nullable=False, unique=True, comment="US, NSE, BSE, MCX, LSE, ..."),
    Column("name", String(200), nullable=False),
    Column("country", String(2), nullable=False),
    Column("market", String(10), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("timezone", String(50), nullable=False, comment="IANA tz e.g. America/New_York"),
    Column("open_time", String(8), nullable=True, comment="HH:MM:SS local"),
    Column("close_time", String(8), nullable=True, comment="HH:MM:SS local"),
    Column("calendar_key", String(20), nullable=True, comment="exchange_calendars lib key: XNYS, XBOM, ..."),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)
