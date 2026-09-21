"""DB-write helpers for political ingestion.

`fast_upsert` is the canonical write path. It auto-picks the fastest mechanism
based on row count:

  < 1K rows         INSERT ... ON CONFLICT  (SQLAlchemy text)         simplest
  1K - 100K rows    psycopg execute_values  (batch INSERT)            ~10× faster
  > 100K rows       psycopg COPY FROM STDIN to temp + INSERT FROM SELECT
                                                                       ~50-100× faster

All paths are idempotent (UPSERT / merge from temp). Re-running same data
yields zero net change.

Follows the style of src/factorlab/storage/ingest.py (engine + rows in,
counts out).
"""

from __future__ import annotations

import io
import logging
from typing import Iterable, Sequence

from sqlalchemy import Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Row-count thresholds picking the write strategy
THRESHOLD_BATCH = 1_000      # below this: ORM-style INSERT
THRESHOLD_COPY = 100_000     # above this: COPY FROM STDIN to temp


def lookup_endpoint_id(engine: Engine, code: str) -> int:
    """Resolve a `ref.data_endpoints.code` string to its integer id.

    Each ingest module calls this ONCE at startup to cache the FK value, then
    puts the resulting int into every row dict's `endpoint_id` field. Replaces
    the freeform `source` String with a proper FK lookup.

    Raises ValueError if `code` doesn't match any active endpoint — fail-fast
    rather than silently dropping rows.
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM ref.data_endpoints WHERE code = :code AND active = true LIMIT 1"
        ), {"code": code}).fetchone()
    if row is None:
        raise ValueError(
            f"no active ref.data_endpoints row matches code={code!r}. "
            f"Add a seed in a migration before this ingest can run."
        )
    return row[0]


def fast_upsert(
    engine: Engine,
    table: Table,
    rows: Sequence[dict],
    *,
    conflict_keys: Sequence[str],
    update_cols: Sequence[str] | None = None,
    batch_size: int = 10_000,
) -> dict[str, int]:
    """Bulk-upsert into a Postgres table.

    Args:
        engine: SQLAlchemy engine (factorlab.storage.db.engine)
        table: SQLAlchemy Table object (one of alt_political_us.*)
        rows: list of dicts, keys matching column names
        conflict_keys: PK / unique-constraint column names that drive ON CONFLICT
        update_cols: which columns to overwrite on conflict (default: all
                     non-conflict columns present in the input rows)
        batch_size: rows per round-trip (mostly relevant for batch path)

    Returns:
        ``{"strategy": "...", "rows_attempted": N, "ok": True}``.
        Per-row counts of inserted-vs-updated are not free in Postgres
        without RETURNING magic, so we just return total attempted.
    """
    rows = list(rows)
    n = len(rows)
    if n == 0:
        return {"strategy": "noop", "rows_attempted": 0, "ok": True}

    if n < THRESHOLD_BATCH:
        return _upsert_orm(engine, table, rows, conflict_keys, update_cols)
    elif n < THRESHOLD_COPY:
        return _upsert_batched(engine, table, rows, conflict_keys, update_cols, batch_size)
    else:
        return _upsert_copy_then_merge(engine, table, rows, conflict_keys, update_cols)


# ---------------------------------------------------------------------------
# Strategy 1: ORM-style insert (small batches)
# ---------------------------------------------------------------------------

def _upsert_orm(
    engine: Engine,
    table: Table,
    rows: Sequence[dict],
    conflict_keys: Sequence[str],
    update_cols: Sequence[str] | None,
) -> dict[str, int]:
    if update_cols is None:
        update_cols = [c for c in rows[0].keys() if c not in conflict_keys]

    stmt = pg_insert(table).values(rows)
    if update_cols:
        update_set = {c: stmt.excluded[c] for c in update_cols if c in table.c}
        stmt = stmt.on_conflict_do_update(index_elements=list(conflict_keys), set_=update_set)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict_keys))

    with engine.begin() as conn:
        conn.execute(stmt)
    log.debug("orm-upsert  table=%s rows=%d", table.name, len(rows))
    return {"strategy": "orm", "rows_attempted": len(rows), "ok": True}


# ---------------------------------------------------------------------------
# Strategy 2: batched executemany (1K - 100K rows)
# ---------------------------------------------------------------------------

def _upsert_batched(
    engine: Engine,
    table: Table,
    rows: Sequence[dict],
    conflict_keys: Sequence[str],
    update_cols: Sequence[str] | None,
    batch_size: int,
) -> dict[str, int]:
    if update_cols is None:
        update_cols = [c for c in rows[0].keys() if c not in conflict_keys]

    cols = list(rows[0].keys())
    fq_table = f'"{table.schema}"."{table.name}"' if table.schema else f'"{table.name}"'
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    conflict_list = ", ".join(f'"{c}"' for c in conflict_keys)
    if update_cols:
        update_clause = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols if c in table.c)
        sql = text(
            f"INSERT INTO {fq_table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_list}) DO UPDATE SET {update_clause}"
        )
    else:
        sql = text(
            f"INSERT INTO {fq_table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_list}) DO NOTHING"
        )

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            conn.execute(sql, chunk)
            total += len(chunk)
    log.debug("batched-upsert  table=%s rows=%d batches=%d",
              table.name, total, (len(rows) + batch_size - 1) // batch_size)
    return {"strategy": "batched", "rows_attempted": total, "ok": True}


# ---------------------------------------------------------------------------
# Strategy 3: COPY to temp + merge (>100K rows)
# ---------------------------------------------------------------------------

def _upsert_copy_then_merge(
    engine: Engine,
    table: Table,
    rows: Sequence[dict],
    conflict_keys: Sequence[str],
    update_cols: Sequence[str] | None,
) -> dict[str, int]:
    """For >100K rows: COPY into a TEMP table, then INSERT ... SELECT ... ON CONFLICT."""
    if update_cols is None:
        update_cols = [c for c in rows[0].keys() if c not in conflict_keys]

    cols = list(rows[0].keys())
    col_list = ", ".join(f'"{c}"' for c in cols)
    fq_table = f'"{table.schema}"."{table.name}"' if table.schema else f'"{table.name}"'
    conflict_list = ", ".join(f'"{c}"' for c in conflict_keys)
    update_clause = (
        ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols if c in table.c)
        if update_cols else None
    )

    # Build TSV stream
    def _val(v):
        if v is None:
            return r"\N"
        s = str(v)
        # Escape tabs/newlines/backslashes for COPY's text format
        return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")

    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join(_val(r.get(c)) for c in cols))
        buf.write("\n")
    buf.seek(0)

    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        # Create a TEMP table mirroring the target's columns
        temp_name = f"_copy_{table.name}_{id(rows) & 0xFFFFFF:x}"
        cur.execute(f'CREATE TEMP TABLE "{temp_name}" (LIKE {fq_table} INCLUDING DEFAULTS) ON COMMIT DROP;')
        # COPY in
        with cur.copy(f'COPY "{temp_name}" ({col_list}) FROM STDIN WITH (FORMAT text, DELIMITER E\'\\t\', NULL \'\\N\')') as copy:
            copy.write(buf.getvalue())
        # Merge into target
        if update_clause:
            cur.execute(
                f"INSERT INTO {fq_table} ({col_list}) "
                f"SELECT {col_list} FROM \"{temp_name}\" "
                f"ON CONFLICT ({conflict_list}) DO UPDATE SET {update_clause}"
            )
        else:
            cur.execute(
                f"INSERT INTO {fq_table} ({col_list}) "
                f"SELECT {col_list} FROM \"{temp_name}\" "
                f"ON CONFLICT ({conflict_list}) DO NOTHING"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()
    log.debug("copy-merge  table=%s rows=%d", table.name, len(rows))
    return {"strategy": "copy_merge", "rows_attempted": len(rows), "ok": True}


# ---------------------------------------------------------------------------
# Convenience: insert-or-ignore (no UPDATE on conflict)
# ---------------------------------------------------------------------------

def bulk_insert_ignore(
    engine: Engine,
    table: Table,
    rows: Sequence[dict],
    *,
    conflict_keys: Sequence[str],
    batch_size: int = 10_000,
) -> dict[str, int]:
    """Insert rows; skip any that hit a conflict on `conflict_keys`. Idempotent."""
    return fast_upsert(
        engine, table, rows,
        conflict_keys=conflict_keys,
        update_cols=[],   # empty list → DO NOTHING path
        batch_size=batch_size,
    )


# ---------------------------------------------------------------------------
# Helper: chunked iterator for streaming sources
# ---------------------------------------------------------------------------

def chunked(iterable: Iterable, size: int):
    """Yield successive `size`-sized chunks from an iterable."""
    chunk: list = []
    for x in iterable:
        chunk.append(x)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
