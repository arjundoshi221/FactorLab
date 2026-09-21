"""Per-filer submissions API (data.sec.gov/submissions).

Returns every filing by a given CIK, most recent 1,000 inline plus paginated
older-history files. Handy for building a company's full filing history
without walking the quarterly full-index.

    from factorlab.sources.edgar import EdgarClient, get_submissions
    client = EdgarClient()
    subs = get_submissions(client, cik=320193)   # AAPL
    recent = subs["recent"]                      # dict of parallel arrays
"""

from __future__ import annotations

import logging
from typing import Any

from factorlab.sources.edgar.client import EdgarClient, cik_padded

log = logging.getLogger(__name__)


def get_submissions(
    client: EdgarClient,
    cik: int | str,
    *,
    include_older: bool = False,
) -> dict[str, Any]:
    """Return the submissions JSON for a filer.

    The JSON has a ``filings`` object with:
      - ``recent`` — parallel arrays (accessionNumber, form, filingDate, ...)
      - ``files`` — list of older-history file references

    When ``include_older`` is True, follows each entry in ``files`` and appends
    its rows to a top-level ``all`` list of dicts (denormalized).
    """
    padded = cik_padded(cik)
    doc = client.get_data(f"/submissions/CIK{padded}.json").json()

    if include_older:
        older = doc.get("filings", {}).get("files", []) or []
        merged = list(_iter_recent_rows(doc))
        for entry in older:
            fname = entry.get("name")
            if not fname:
                continue
            extra = client.get_data(f"/submissions/{fname}").json()
            merged.extend(_iter_recent_rows({"filings": {"recent": extra}}))
        doc["all"] = merged

    return doc


def _iter_recent_rows(doc: dict[str, Any]):
    """Zip the parallel arrays under filings.recent into per-filing dicts."""
    recent = doc.get("filings", {}).get("recent", {}) or {}
    if not recent:
        return
    keys = list(recent.keys())
    lengths = {k: len(recent[k]) for k in keys if isinstance(recent[k], list)}
    if not lengths:
        return
    n = min(lengths.values())
    for i in range(n):
        yield {k: recent[k][i] for k in lengths}
