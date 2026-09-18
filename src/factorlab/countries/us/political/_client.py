"""Political HTTP client — thin specialisation over :class:`HTTPClient`.

The generic retry / disk-cache / audit-row plumbing lives in
:mod:`factorlab.shared.ingest.http`. This module pins the political-side
defaults:

  - all caches live under ``raw_dir('political')`` (legacy nested layout)
  - audit rows write to ``audit.raw_archive`` keyed on the political
    ``ref.data_endpoints`` codes

Existing callers across ``congress_gov``, ``fec``, ``house_clerk``,
``senate_efd``, ``lda``, ``usaspending``, ``finnhub_contracts`` keep
``from factorlab.countries.us.political._client import PoliticalHTTPClient,
RateLimitDeferred`` unchanged.
"""

from __future__ import annotations

from pathlib import Path

from factorlab.shared.ingest.http import HTTPClient, RateLimitDeferred  # noqa: F401
from factorlab.shared.paths import REPO_ROOT, raw_dir  # noqa: F401

# Where political-domain caches live on disk. Module-level so legacy callers
# that reference ``DATA_ROOT`` directly keep working.
DATA_ROOT = raw_dir("political")


def data_path(source: str, *parts: str) -> Path:
    """Compute the on-disk path for a fetched response under
    ``data/political/raw/{source}/...``.

    Kept at module scope for back-compat with the original
    ``data_path('lda', 'index', f'{year}.json')`` calls.
    """
    p = DATA_ROOT / source
    for part in parts:
        p = p / part
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class PoliticalHTTPClient(HTTPClient):
    """``HTTPClient`` pinned to the political-domain cache root.

    Constructor signature matches the legacy class verbatim so every existing
    call site continues to work without edits.
    """

    def __init__(
        self,
        source: str,
        *,
        engine=None,
        country_code: str = "US",
        write_archive: bool = True,
        retries: int = 3,
        backoff_base: float = 2.0,
        timeout: int = 180,
        headers: dict[str, str] | None = None,
        min_interval_sec: float = 0.0,
        max_retry_after_sec: float = 600.0,
        single_key_inline_sleep: bool = False,
    ) -> None:
        super().__init__(
            source=source,
            data_root=DATA_ROOT,
            engine=engine,
            country_code=country_code,
            write_archive=write_archive,
            retries=retries,
            backoff_base=backoff_base,
            timeout=timeout,
            headers=headers,
            min_interval_sec=min_interval_sec,
            max_retry_after_sec=max_retry_after_sec,
            single_key_inline_sleep=single_key_inline_sleep,
        )


__all__ = [
    "PoliticalHTTPClient",
    "RateLimitDeferred",
    "DATA_ROOT",
    "data_path",
    "REPO_ROOT",
]
