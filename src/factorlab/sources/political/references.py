"""Current US legislator, committee, and membership reference data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from factorlab.storage.clickhouse import ClickHouseStorage

_BASE_URL = "https://unitedstates.github.io/congress-legislators"
_URLS = {
    "legislators": f"{_BASE_URL}/legislators-current.json",
    "committees": f"{_BASE_URL}/committees-current.json",
    "memberships": f"{_BASE_URL}/committee-membership-current.json",
}


@dataclass(frozen=True)
class PoliticalReferenceSnapshot:
    legislators: list[dict[str, Any]]
    committees: list[dict[str, Any]]
    memberships: dict[str, list[dict[str, Any]]]
    raw_ids: dict[str, Any]


def fetch_reference_snapshot(
    storage: ClickHouseStorage,
    *,
    session: requests.Session | None = None,
) -> PoliticalReferenceSnapshot:
    """Fetch and archive the three current congressional reference datasets."""
    http = session or requests.Session()
    datasets: dict[str, Any] = {}
    raw_ids = {}
    for name, url in _URLS.items():
        response = http.get(url, timeout=60)
        raw_ids[name] = storage.archive_http_response(
            source=f"congress_legislators_{name}",
            source_url=url,
            response_body=response.content,
            status_code=response.status_code,
            response_headers=dict(response.headers),
            fetch_key=name,
            content_type=response.headers.get("Content-Type", "application/json"),
            metadata={"dataset": name},
        )
        response.raise_for_status()
        datasets[name] = json.loads(response.content)

    return PoliticalReferenceSnapshot(
        legislators=datasets["legislators"],
        committees=datasets["committees"],
        memberships=datasets["memberships"],
        raw_ids=raw_ids,
    )

