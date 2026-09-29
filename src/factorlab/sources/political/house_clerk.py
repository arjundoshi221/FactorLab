"""House Clerk filing-index and PTR fetchers (legacy bootstrap path).

Parsing moved to :mod:`factorlab.sources.house_clerk.parse` (re-exported here);
the dataset sources live in :mod:`factorlab.sources.house_clerk.sources`. These
fetchers remain for ``scripts/factlab_political_bootstrap.py`` until the P5
cutover; ``storage`` is any object with ``archive_http_response`` (07 rule R1).
"""

from __future__ import annotations

from typing import Any

import requests

from factorlab.sources.house_clerk.parse import (
    _INDEX_URL,
    _PTR_URL,
    PARSER_VERSION,
    parse_house_filing_index,
    parse_house_ptr_text,
    pdf_text,
)

__all__ = [
    "PARSER_VERSION",
    "fetch_and_parse_ptr",
    "fetch_house_filing_index",
    "parse_house_filing_index",
    "parse_house_ptr_text",
]

_ = _PTR_URL  # re-exported for callers that build PTR URLs


def fetch_house_filing_index(
    year: int,
    storage: Any,
    *,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Fetch, archive, and parse the annual House financial-disclosure index."""
    http = session or requests.Session()
    url = _INDEX_URL.format(year=year)
    response = http.get(url, timeout=60)
    raw_id = storage.archive_http_response(
        source="house_clerk_filing_index",
        source_url=url,
        response_body=response.content,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        fetch_key=str(year),
        content_type=response.headers.get("Content-Type", "application/zip"),
        metadata={"filing_year": year},
    )
    response.raise_for_status()
    return parse_house_filing_index(response.content, year), raw_id


def fetch_and_parse_ptr(
    filing: dict[str, Any],
    storage: Any,
    *,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Fetch one PTR PDF, archive it, extract text, and parse transactions."""
    http = session or requests.Session()
    url = filing["filing_url"]
    response = http.get(url, timeout=60)
    raw_id = storage.archive_http_response(
        source="house_clerk_ptr_pdf",
        source_url=url,
        response_body=response.content,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        fetch_key=filing["filing_id"],
        content_type=response.headers.get("Content-Type", "application/pdf"),
        metadata={
            "filing_id": filing["filing_id"],
            "filing_year": filing["filing_year"],
        },
    )
    response.raise_for_status()
    return parse_house_ptr_text(pdf_text(response.content), filing), raw_id
