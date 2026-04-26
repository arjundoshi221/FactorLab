"""Reusable column factories for time-series tables.

Every fact table in FactorLab carries a standard tail:
  market       — which market produced this row ('us', 'in', 'eu', ...)
  currency     — ISO 4217 (USD, INR, EUR, ...)
  source       — which adapter wrote it ('eodhd', 'upstox', 'ibkr', ...)
  as_of_time   — point-in-time: when WE ingested / knew this fact
  created_at   — row insert time (server default)
  updated_at   — last modification (server default, auto-update via trigger)
"""

from sqlalchemy import Column, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID


def pk_uuid(name: str = "id") -> Column:
    """UUID primary key with server-side default."""
    return Column(name, UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def col_market() -> Column:
    return Column("market", String(10), nullable=False, index=True, comment="Market code: us, in, eu, gb, ...")


def col_currency() -> Column:
    return Column("currency", String(3), nullable=False, comment="ISO 4217 currency code")


def col_source() -> Column:
    return Column("source", String(50), nullable=False, comment="Adapter that wrote this row")


def col_as_of_time() -> Column:
    """Point-in-time timestamp: when we ingested this fact."""
    return Column("as_of_time", DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


def col_created_at() -> Column:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


def col_updated_at() -> Column:
    return Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


def timestamps() -> list[Column]:
    """Standard created_at + updated_at pair."""
    return [col_created_at(), col_updated_at()]


def audit_cols() -> list[Column]:
    """Full audit tail: market + currency + source + as_of_time + created/updated."""
    return [col_market(), col_currency(), col_source(), col_as_of_time(), col_created_at(), col_updated_at()]
