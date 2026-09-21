"""SEC EDGAR HTTP client with UA-authenticated session, throttling, and retries.

Usage:
    from factorlab.sources.edgar.client import EdgarClient
    client = EdgarClient()                   # UA from EDGAR_USER_AGENT env var
    resp = client.get_www("/Archives/edgar/full-index/2026/QTR3/form.idx")
    data = client.get_data("/submissions/CIK0000320193.json").json()

SEC Fair Access policy requires a self-identifying User-Agent on every request.
The client raises OSError if EDGAR_USER_AGENT is missing.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests
from dotenv import find_dotenv, load_dotenv

from factorlab.core.secrets import get_secret

log = logging.getLogger(__name__)

BASE_URL_WWW = "https://www.sec.gov"
BASE_URL_DATA = "https://data.sec.gov"
BASE_URL_SEARCH = "https://efts.sec.gov"

# SEC ceiling is 10 req/sec, per-IP, across all hosts. 100 ms floor keeps us safe.
MIN_REQUEST_INTERVAL = 0.1
RETRY_ATTEMPTS = 5
REQUEST_TIMEOUT = 60


class EdgarClient:
    """Thin, throttled wrapper over the three EDGAR hosts.

    All GETs share one throttle since the 10 rps cap is per-IP, not per-host.
    Optional ``storage`` hook mirrors the EODHD pattern for raw response archival.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        storage=None,
        sleep=time.sleep,
    ):
        load_dotenv(find_dotenv(usecwd=True))
        ua = (user_agent or get_secret("EDGAR_USER_AGENT", "") or "").strip()
        if not ua:
            raise OSError(
                "EDGAR_USER_AGENT is missing. SEC requires an identifying "
                "User-Agent on every request. Set EDGAR_USER_AGENT in .env, "
                'format: "Sample Company Name AdminContact@samplecompany.com"'
            )
        self.user_agent = ua
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ua,
            "Accept-Encoding": "gzip, deflate",
        })
        self._last_request_at: float = 0.0
        self.storage = storage
        self.sleep = sleep
        self.last_raw_id = None

    # ── internals ────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            self.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def _archive(self, url: str, resp: requests.Response, fetch_key: str) -> None:
        if self.storage is None:
            self.last_raw_id = None
            return
        self.last_raw_id = self.storage.archive_http_response(
            source="edgar",
            source_url=url,
            response_body=resp.content,
            status_code=resp.status_code,
            response_headers=dict(resp.headers),
            fetch_key=fetch_key,
            content_type=resp.headers.get("Content-Type", "application/octet-stream"),
            metadata=None,
        )

    def _backoff_delay(self, attempt: int, resp: requests.Response | None) -> float:
        delay: float = 2 ** attempt
        if resp is None:
            return delay
        retry = resp.headers.get("Retry-After")
        if not retry:
            return delay
        try:
            return max(delay, float(retry))
        except ValueError:
            try:
                return max(
                    delay,
                    (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds(),
                )
            except (ValueError, TypeError):
                return delay

    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        headers: dict | None = None,
        fetch_key: str | None = None,
    ) -> requests.Response:
        """Throttled GET with retry and optional raw-archive hook.

        ``url`` must be absolute. Use ``get_www`` / ``get_data`` / ``get_search``
        for host-scoped paths.
        """
        merged_headers = dict(headers or {})
        for attempt in range(RETRY_ATTEMPTS):
            self._throttle()
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=merged_headers or None,
                    timeout=REQUEST_TIMEOUT,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                self.sleep(2 ** attempt)
                continue
            self._last_request_at = time.monotonic()
            self._archive(url, resp, fetch_key or url)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == RETRY_ATTEMPTS - 1:
                    resp.raise_for_status()
                self.sleep(max(0.0, self._backoff_delay(attempt, resp)))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("EDGAR retry budget exhausted")

    # ── host-scoped helpers ─────────────────────────────────────────────

    def get_www(self, path: str, params: dict | None = None, **kwargs) -> requests.Response:
        """GET on www.sec.gov. ``path`` starts with '/'."""
        # www.sec.gov requires an explicit Host header on some endpoints (safe default)
        headers = kwargs.pop("headers", None) or {}
        headers.setdefault("Host", "www.sec.gov")
        return self.get(f"{BASE_URL_WWW}{path}", params, headers=headers, **kwargs)

    def get_data(self, path: str, params: dict | None = None, **kwargs) -> requests.Response:
        """GET on data.sec.gov. ``path`` starts with '/'."""
        headers = kwargs.pop("headers", None) or {}
        headers.setdefault("Host", "data.sec.gov")
        return self.get(f"{BASE_URL_DATA}{path}", params, headers=headers, **kwargs)

    def get_search(self, path: str, params: dict | None = None, **kwargs) -> requests.Response:
        """GET on efts.sec.gov (full-text search). ``path`` starts with '/'."""
        return self.get(f"{BASE_URL_SEARCH}{path}", params, **kwargs)


# ── helpers ─────────────────────────────────────────────────────────────

def cik_padded(cik: int | str) -> str:
    """Zero-pad a CIK to 10 digits (URL format for submissions + companyfacts)."""
    return f"{int(cik):010d}"


def accession_nodash(accession: str) -> str:
    """Strip dashes from an accession number (URL path format)."""
    return accession.replace("-", "")
