"""universe schema — index definitions and point-in-time membership."""

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Index,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import col_created_at, col_updated_at

SCHEMA = "universe"
INST_FK = "ref.instruments.id"

# ---------------------------------------------------------------------------
# universe.indexes — named universes / indices
# ---------------------------------------------------------------------------
indexes = Table(
    "indexes",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", String(100), nullable=False, unique=True, comment="nifty50, sp500, fo_eligible"),
    Column("display_name", String(200), nullable=True, comment="NIFTY 50, S&P 500"),
    Column("market_code", String(10), ForeignKey("ref.markets.code"), nullable=False),
    Column("index_type", String(30), nullable=False, server_default="benchmark",
           comment="benchmark, custom, derived, sector"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# universe.members — point-in-time membership
# Query: WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)
# ---------------------------------------------------------------------------
members = Table(
    "members",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("index_id", UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.indexes.id"), nullable=False),
    Column("instrument_id", UUID(as_uuid=True), ForeignKey(INST_FK), nullable=False),
    Column("start_date", Date, nullable=False, comment="Date instrument entered the index"),
    Column("end_date", Date, nullable=True, comment="Date left — NULL = current member"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
    comment="Point-in-time membership. Query: WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)",
)

Index("ix_members_index_date", members.c.index_id, members.c.start_date, members.c.end_date)
Index("ix_members_instrument_date", members.c.instrument_id, members.c.start_date, members.c.end_date)
