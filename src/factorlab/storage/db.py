"""Legacy SQLAlchemy metadata for the normalized Postgres design.

The current ClickHouse target is documented in
``docs/architecture/02-database-clickhouse.md``. Until migration is complete,
the Postgres schemas below follow ``docs/architecture/database.md``:
  ref            — securities, calendars, exchanges
  audit          — ingestion and provenance records
  market_in      — India prices and derivatives
  market_us      — US prices, corporate actions, fundamentals, IBKR mirrors
  alt_social     — reddit, twitter posts and derived signals
  alt_political_us  — senator/house trades, committees
  alt_research   — arxiv papers, embeddings, summaries
  derived        — factors, signals, portfolios
  experiments    — research runs, backtest results, proposed orders
"""

import os

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Naming conventions for consistent constraint names across migrations
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

# Postgres schema names — per-country split for market + alt_political_us domains
SCHEMAS = [
    "ref",
    "audit",
    "market_in",
    "market_us",
    "universe",
    "alt_social",
    "alt_political_us",
    "alt_research",
    "derived",
    "experiments",
]


def get_engine(url: str | None = None, echo: bool = False):
    """Create a SQLAlchemy engine from DATABASE_URL."""
    return create_engine(url or DATABASE_URL, echo=echo)


def get_session(url: str | None = None) -> Session:
    """Create a one-off session (for scripts / notebooks)."""
    return Session(get_engine(url))
