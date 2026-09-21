"""EDGAR full-text search (efts.sec.gov/LATEST/search-index).

Indexed since 2001. Returns JSON hits with accession, form, filer, and a
highlighted snippet. Use for *discovery* ("who mentioned NVDA in risk factors"),
not enumeration — the full-index is authoritative for the latter.

    from factorlab.sources.edgar import EdgarClient, search
    client = EdgarClient()
    hits = search(client, q='"single-stock leveraged"', forms=["N-1A"])
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from factorlab.sources.edgar.client import EdgarClient


def search(
    client: EdgarClient,
    q: str,
    *,
    forms: Iterable[str] | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    ciks: Iterable[int | str] | None = None,
    page_from: int = 0,
) -> dict[str, Any]:
    """Query EDGAR full-text search.

    Args:
        q: Query string. Wrap phrases in double quotes.
        forms: Restrict to given form types (e.g. ["10-K", "10-Q"]).
        date_from / date_to: 'YYYY-MM-DD' inclusive range.
        ciks: Restrict to given filers.
        page_from: Offset for pagination (page size is 10; server-capped).

    Returns the raw response JSON (``hits.hits`` holds the results).
    """
    params: dict[str, Any] = {"q": q, "from": page_from}
    if forms:
        params["forms"] = ",".join(forms)
    if date_from or date_to:
        params["dateRange"] = "custom"
        if date_from:
            params["startdt"] = _fmt_date(date_from)
        if date_to:
            params["enddt"] = _fmt_date(date_to)
    if ciks:
        params["ciks"] = ",".join(f"{int(c):010d}" for c in ciks)
    return client.get_search("/LATEST/search-index", params=params).json()


def _fmt_date(d: date | str) -> str:
    return d if isinstance(d, str) else d.strftime("%Y-%m-%d")
