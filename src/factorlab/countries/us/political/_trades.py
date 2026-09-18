"""Shared helpers for any source that writes to `legislator_trades`.

Replaces ~250 LOC of near-identical boilerplate previously duplicated across
house_clerk/, senate_stock_watcher/, and senate_efd/ ingest modules:

  - AMOUNT_BUCKETS constant + parse_amount(s)
  - parse_us_date / parse_us_or_iso_date / parse_iso_date
  - flush_legislator_trades(engine, rows) — dedups within batch + UPSERTs

Strict-NULL policy: every helper returns None on miss, never a fabricated
default. Callers that previously did `.get("...", "purchase")` must drop
the default — see `_constants.LEGISLATOR_TRADES_*` for the canonical
upsert keys.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable, Sequence

from sqlalchemy.engine import Engine

from factorlab.countries.us.political._constants import (
    LEGISLATOR_TRADES_CONFLICT_KEYS,
    LEGISLATOR_TRADES_UPDATE_COLS,
)
from factorlab.countries.us.political._db import fast_upsert

log = logging.getLogger(__name__)


# ---- amount bucket parsing -------------------------------------------------

# 10 STOCK Act disclosure brackets. Used by every PTR source.
AMOUNT_BUCKETS: dict[str, tuple[int, int | None]] = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, None),
}


def parse_amount(s: str | None) -> tuple[int | None, int | None, int | None]:
    """Return (lo, hi, mid) for a STOCK Act bucket string.

    Tolerant of whitespace + dollar-sign variations. Returns (None, None, None)
    if no bucket matches — strict-NULL caller responsibility.
    """
    if not s:
        return None, None, None
    import re

    s_clean = re.sub(r"\s+", " ", s.replace("\n", " ")).strip().rstrip("-").strip()
    norm = s_clean.replace(" ", "")
    for k, (lo, hi) in AMOUNT_BUCKETS.items():
        if k.replace(" ", "") == norm:
            mid = ((lo + hi) // 2) if hi else lo
            return lo, hi, mid
    # Tolerant pattern: '$X - $Y' with arbitrary whitespace
    m = re.search(r"\$([\d,]+)\s*-\s*\$?([\d,]+)", s_clean)
    if m:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        return lo, hi, (lo + hi) // 2
    m = re.search(r"Over\s*\$([\d,]+)", s_clean, flags=re.IGNORECASE)
    if m:
        lo = int(m.group(1).replace(",", ""))
        return lo, None, lo
    return None, None, None


# ---- date parsing ----------------------------------------------------------

def parse_us_date(s: str | None) -> date | None:
    """`MM/DD/YYYY` -> date, or None on miss / blank."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except Exception:
        return None


def parse_iso_date(s: str | None) -> date | None:
    """`YYYY-MM-DD` -> date, or None on miss / blank."""
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except Exception:
        return None


def parse_us_or_iso_date(s: str | None) -> date | None:
    """Try US format first, then ISO. Returns None on miss."""
    return parse_us_date(s) or parse_iso_date(s)


# ---- file-text mappings ----------------------------------------------------

# House Clerk single-character codes
OWNER_CODE_TO_FILER_TYPE: dict[str | None, str] = {
    "SP": "spouse",
    "JT": "joint",
    "DC": "dependent_child",
    "JR": "junior",
    None: "self",
}

TX_TYPE_CODE_TO_NAME: dict[str, str] = {
    "P": "purchase",
    "S": "sale_full",
    "E": "exchange",
}

# SSW / Senate eFD long-form text
OWNER_TEXT_TO_FILER_TYPE: dict[str, str] = {
    "Self": "self",
    "self": "self",
    "Spouse": "spouse",
    "Joint": "joint",
    "Child": "dependent_child",
    "Dependent Child": "dependent_child",
    "N/A": "self",
    "--": "self",
    "": "self",
}

# Strict-NULL: only known mappings. Returns None on unknown — caller must
# leave the column NULL rather than fabricate "purchase".
TX_TYPE_TEXT_TO_NAME: dict[str, str] = {
    "Purchase": "purchase",
    "Sale (Full)": "sale_full",
    "Sale (Partial)": "sale_partial",
    "Exchange": "exchange",
}


# ---- the canonical flush -------------------------------------------------

def dedup_trades(rows: Iterable[dict]) -> list[dict]:
    """Within-batch dedup using the LEGISLATOR_TRADES_CONFLICT_KEYS tuple.

    Some PTRs list the same trade twice (same date, asset, type, amount).
    The Postgres ON CONFLICT clause cannot update one target twice in a
    single statement, so we collapse duplicates client-side.
    Last-wins.
    """
    seen: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r.get(c) for c in LEGISLATOR_TRADES_CONFLICT_KEYS)
        seen[k] = r
    return list(seen.values())


def flush_legislator_trades(
    engine: Engine,
    rows: Sequence[dict],
) -> dict[str, int]:
    """Dedup + UPSERT a batch of legislator_trades rows.

    Replaces the per-source `_flush()` boilerplate that was duplicated 3x.
    Pure pass-through to `fast_upsert` with the canonical conflict keys
    and update cols from `_constants`.

    Returns the dict from `fast_upsert` (`{strategy, rows_attempted, ok}`).
    Empty input is a no-op.
    """
    from factorlab.storage.schemas.alt_political_us import legislator_trades

    if not rows:
        return {"strategy": "noop", "rows_attempted": 0, "ok": True}

    unique = dedup_trades(rows)
    if len(unique) < len(rows):
        log.debug("dedup-trades collapsed %d -> %d", len(rows), len(unique))

    return fast_upsert(
        engine,
        legislator_trades,
        unique,
        conflict_keys=list(LEGISLATOR_TRADES_CONFLICT_KEYS),
        update_cols=list(LEGISLATOR_TRADES_UPDATE_COLS),
    )
