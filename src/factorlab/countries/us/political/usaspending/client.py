"""USASpending.gov client — POST /api/v2/search/spending_by_award/."""

from __future__ import annotations

import logging
from pathlib import Path

from factorlab.countries.us.political._client import PoliticalHTTPClient

log = logging.getLogger(__name__)

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]

SEARCH_FIELDS = [
    "Award ID", "generated_internal_id", "Recipient Name", "recipient_id",
    "Awarding Agency", "Awarding Sub Agency", "Award Amount",
    "Description", "Start Date", "End Date", "NAICS",
    "Place of Performance City Code", "Place of Performance State Code",
    "Place of Performance Country Code",
]


def search_recipient_page(
    http: PoliticalHTTPClient,
    *,
    recipient: str,
    fy: int,
    page: int,
    limit: int = 100,
) -> dict:
    body = {
        "filters": {
            "recipient_search_text": [recipient],
            "award_type_codes": CONTRACT_TYPE_CODES,
            "time_period": [{"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}],
        },
        "fields": SEARCH_FIELDS,
        "page": page,
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
    }
    safe = "".join(ch if ch.isalnum() else "_" for ch in recipient)[:60].upper()
    save_as = Path("awards") / f"{safe}_FY{fy}_p{page}.json"
    return http.post_json(URL, json_body=body, save_as=save_as)


def iter_contracts_for_recipient(
    http: PoliticalHTTPClient,
    *,
    recipient: str,
    fy: int,
    max_pages: int = 20,
):
    page = 1
    while page <= max_pages:
        data = search_recipient_page(http, recipient=recipient, fy=fy, page=page)
        results = data.get("results") or []
        for row in results:
            yield row
        meta = data.get("page_metadata") or {}
        if not results or not meta.get("hasNext"):
            break
        page += 1
