"""LDA REST client thin wrapper around `_client.PoliticalHTTPClient`.

Auth: optional bearer-style token from LDA_API_TOKEN. Without a token, LDA
caps anonymous traffic at ~15 req/min per IP. With a token (free, register
at https://lda.senate.gov/api/auth/register/), the cap rises to ~120 req/min.

LDA does NOT participate in api.data.gov key pooling — its tokens are
LDA-only. Single-key clients use the `single_key_inline_sleep=True` mode of
PoliticalHTTPClient: on 429 we inline-sleep Retry-After and continue,
instead of deferring (which is the right behaviour for FEC/Congress where
key rotation is possible).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from dotenv import find_dotenv, load_dotenv

from factorlab.countries.us.political._client import PoliticalHTTPClient

load_dotenv(find_dotenv(usecwd=True))

log = logging.getLogger(__name__)

BASE = "https://lda.senate.gov/api/v1"

# Pace requests so we stay under the per-minute cap with headroom.
# Anonymous: 15/min → 4.0s; authenticated: 120/min → 0.5s.
MIN_INTERVAL_AUTH_SEC = 0.55
MIN_INTERVAL_ANON_SEC = 4.1

# Pages of filings for the current calendar year are mutable (registrants
# file post-deadline amendments daily). Cache TTL forces a refresh on each
# new run. Prior-year pages are treated as immutable archives.
# Override via `POLITICAL_CACHE_TTL_ACTIVE_SEC` env var.
CURRENT_YEAR_CACHE_TTL_SEC: float = float(
    os.getenv("POLITICAL_CACHE_TTL_ACTIVE_SEC", "3600")
)


def get_api_token() -> str | None:
    tok = (os.getenv("LDA_API_TOKEN") or "").strip()
    return tok or None


class LDAClient:
    def __init__(self, http: PoliticalHTTPClient) -> None:
        self.http = http
        token = get_api_token()
        if token:
            self.http.session.headers["Authorization"] = f"Token {token}"
            target = MIN_INTERVAL_AUTH_SEC
            log.info("[lda] auth: token present (~120 req/min cap)")
        else:
            target = MIN_INTERVAL_ANON_SEC
            log.warning("[lda] auth: no LDA_API_TOKEN — anonymous, ~15 req/min cap. "
                        "Register at https://lda.senate.gov/api/auth/register/")
        if self.http.min_interval_sec < target:
            self.http.min_interval_sec = target
        # Single-key client — no rotation possible. On 429, inline-sleep.
        self.http.single_key_inline_sleep = True

    def get_filings_page(self, *, page: int, page_size: int = 100, **filters) -> dict:
        params = {"page": page, "page_size": page_size, **filters}
        filing_year = filters.get("filing_year", "all")
        save_as = Path("filings") / f"{filing_year}_{filters.get('filing_period', 'all')}_p{page}.json"
        # Current-year pages are mutable (new filings + amendments daily);
        # apply a TTL so subsequent runs see fresh data. Closed years cache forever.
        current_year = datetime.now(timezone.utc).year
        max_age = (CURRENT_YEAR_CACHE_TTL_SEC
                   if isinstance(filing_year, int) and filing_year == current_year
                   else None)
        return self.http.get_json(f"{BASE}/filings/", params=params,
                                  save_as=save_as, max_age_sec=max_age)

    def iter_filings(self, *, page_size: int = 100, max_pages: int | None = None,
                     **filters) -> Iterator[dict]:
        """Yield every filing matching `filters`, paginating until done."""
        page = 1
        while True:
            data = self.get_filings_page(page=page, page_size=page_size, **filters)
            results = data.get("results") or []
            if not results:
                break
            yield from results
            if not data.get("next") or (max_pages and page >= max_pages):
                break
            page += 1
            time.sleep(0.05)
