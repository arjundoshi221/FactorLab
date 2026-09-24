"""GitHub-hosted CSV universe adapter."""

from __future__ import annotations

import csv
import io
from urllib.parse import urlparse

import requests

from factorlab.universe.base import UniverseResolver


def _validate_github_url(value: str) -> None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {"github.com", "raw.githubusercontent.com"}:
        raise ValueError("GitHub CSV URLs must use HTTPS on an approved GitHub host")


class GithubCsvUniverseResolver(UniverseResolver):
    provider = "github_csv"

    def __init__(self, *, indexes, validator, storage=None, session=None):
        super().__init__(validator=validator)
        self.indexes = indexes
        self.storage = storage
        self.session = session or requests.Session()

    def _retrieve_index(self, name: str):
        mapping = self.indexes[name]
        url = str(mapping.url)
        _validate_github_url(url)
        try:
            response = self.session.get(url, timeout=30, allow_redirects=True)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise RuntimeError(f"GitHub CSV request failed for {name}") from exc
        _validate_github_url(response.url)
        raw_id = None
        if self.storage is not None:
            raw_id = self.storage.archive_http_response(
                source="github_csv", source_url=response.url,
                response_body=response.content, status_code=response.status_code,
                content_type=response.headers.get("Content-Type", "text/csv"),
                metadata={"index": name},
            )
        if not response.ok:
            raise RuntimeError(f"GitHub CSV request failed for {name}: HTTP {response.status_code}")
        try:
            reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
            if not reader.fieldnames or mapping.symbol_column not in reader.fieldnames:
                raise ValueError(
                    f"GitHub CSV for {name} has no {mapping.symbol_column!r} column"
                )
            symbols = []
            for row in reader:
                value = row.get(mapping.symbol_column)
                if not value or not str(value).strip():
                    raise ValueError(f"GitHub CSV for {name} contains an empty symbol")
                symbols.append(value)
        except UnicodeDecodeError as exc:
            raise ValueError(f"GitHub CSV for {name} is not UTF-8") from exc
        if not symbols:
            raise ValueError(f"GitHub CSV for {name} returned no constituents")
        self._provenance.setdefault("indexes", {})[name] = {
            "url": response.url, "raw_id": str(raw_id) if raw_id else None,
        }
        return symbols
