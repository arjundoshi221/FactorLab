"""universe schema — index definitions and point-in-time membership."""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import col_created_at, col_updated_at

SCHEMA = "universe"
SEC_FK = "ref.securities.id"

# ---------------------------------------------------------------------------
# universe.indexes — named universes / indices
# e.g. "S&P 500", "Russell 3000", "NIFTY 50", "my_value_screen"
# ---------------------------------------------------------------------------
indexes = Table(
    "indexes",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", String(100), nullable=False, unique=True, comment="S&P 500, Russell 3000, NIFTY 50, custom screens"),
    Column("description", Text, nullable=True),
    Column("market", String(10), nullable=False, comment="us, in, eu, global"),
    Column("index_type", String(30), nullable=False, server_default="benchmark",
           comment="benchmark, custom, sector, factor"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)

# ---------------------------------------------------------------------------
# universe.membership — point-in-time index/universe membership
# Query pattern: WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)
# ---------------------------------------------------------------------------
membership = Table(
    "membership",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("index_id", UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.indexes.id"), nullable=False),
    Column("security_id", UUID(as_uuid=True), ForeignKey(SEC_FK), nullable=False),
    Column("start_date", Date, nullable=False, comment="Date security entered the index"),
    Column("end_date", Date, nullable=True, comment="Date security left — null = current member"),
    Column("weight", String(20), nullable=True, comment="Weight if available, e.g. market-cap weight"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
    comment="Point-in-time membership. Never ask 'is AAPL in S&P 500?' — ask 'was AAPL in S&P 500 on 2024-03-15?'",
)

# Primary lookup: "who was in index X on date Y?"
Index("ix_membership_index_date", membership.c.index_id, membership.c.start_date, membership.c.end_date)
# Reverse lookup: "which indexes was security X in on date Y?"
Index("ix_membership_security_date", membership.c.security_id, membership.c.start_date, membership.c.end_date)
