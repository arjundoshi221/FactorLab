"""audit schema — unified raw-payload audit trail.

One table (`audit.raw_archive`) replaces the previous per-domain audit tables
(`market.raw_responses`, `alt_political.raw_archive`). Every ingest module
writes here. Every row carries `endpoint_id` FK; vendor reached via JOIN to
`ref.vendors`.
"""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Table,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from factorlab.storage.db import metadata

SCHEMA = "audit"
ENDPOINT_FK = "ref.data_endpoints.id"


# ---------------------------------------------------------------------------
# audit.raw_archive — one row per fetch / scrape / file-read across all sources
# ---------------------------------------------------------------------------
raw_archive = Table(
    "raw_archive",
    metadata,
    Column("raw_id", UUID(as_uuid=True), primary_key=True,
           server_default=text("gen_random_uuid()")),
    Column("endpoint_id", Integer, ForeignKey(ENDPOINT_FK), nullable=False,
           comment="Which feed this fetch came from. JOIN to ref.vendors via FK chain."),
    Column("fetched_at", DateTime(timezone=True), nullable=False,
           comment="When the fetch happened."),
    Column("source_url", String(800), nullable=True,
           comment="Full request URL — already SECRET-scrubbed at write time."),
    Column("status_code", SmallInteger, nullable=True,
           comment="HTTP status; NULL for non-HTTP fetches (file reads, scrapes)."),
    Column("content_type", String(80), nullable=True),
    Column("body_bytes", Integer, nullable=True,
           comment="Decompressed body size in bytes."),
    Column("response_headers", JSONB, nullable=True,
           comment="Filtered HTTP response headers."),
    Column("response_bytes", LargeBinary, nullable=True,
           comment="Raw response body — only when configured to retain (rare)."),
    Column("payload", JSONB, nullable=True,
           comment="Structured response (preferred over raw bytes for JSON/XML APIs)."),
    Column("instrument_key", String(100), nullable=True,
           comment="Optional: the instrument this fetch was for (market data)."),
    Column("row_count", Integer, nullable=True,
           comment="Optional: number of rows extracted from this response."),
    Column("parsed_into", String(80), nullable=True,
           comment="Optional: target table name (e.g. 'legislator_trades')."),
    Column("metadata_json", JSONB, nullable=True,
           comment="Free-form per-source metadata (cache hit, retry count, etc.)."),
    Column("created_at", DateTime(timezone=True), nullable=False,
           server_default=func.now()),
    schema=SCHEMA,
    comment="Unified raw-payload audit. Every ingest writes here.",
)

Index("ix_raw_archive_endpoint", raw_archive.c.endpoint_id)
Index("ix_raw_archive_fetched", raw_archive.c.fetched_at)
Index("ix_raw_archive_parsed_into", raw_archive.c.parsed_into,
      postgresql_where=raw_archive.c.parsed_into.isnot(None))
Index("ix_raw_archive_instrument", raw_archive.c.instrument_key,
      postgresql_where=raw_archive.c.instrument_key.isnot(None))
