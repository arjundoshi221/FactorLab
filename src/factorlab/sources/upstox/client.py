"""Upstox transport: auth, rate limiting and HTTP -> error-taxonomy mapping.

Only this module talks to the network. It returns :class:`RawCapture` objects
holding the response bytes exactly as received; interpretation happens in
``normalize.py``. The access token is re-read through ``get_secret`` before
every request so a token rotated by the Cloudflare secrets agent (05) is
adopted without restarting, and each instance reads its own secret name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import requests

from factorlab.core.secrets import get_secret
from factorlab.ingest.errors import AuthRequired, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.ingest.transport import raise_for_status, retry_after_seconds
from factorlab.sources.upstox.settings import UpstoxSettings


class UpstoxClient:
    def __init__(self, settings: UpstoxSettings, *, session: requests.Session | None = None,
                 limiter: SlidingWindowLimiter | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 secret: Callable[[str, str], str | None] = get_secret) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.limiter = limiter or SlidingWindowLimiter(settings.rate_limits.windows())
        self._clock = clock
        self._secret = secret
        self._token = ""

    def _token_now(self) -> str:
        token = (self._secret(self.settings.api.token_env, "") or "").strip()
        if not token:
            raise AuthRequired(f"{self.settings.api.token_env} is missing or empty")
        self._token = token
        return token

    def _request(self, url: str, *, auth: bool) -> requests.Response:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token_now()}"
        self.limiter.acquire()
        try:
            return self.session.get(url, headers=headers, timeout=self.settings.api.timeout)
        except requests.RequestException as exc:
            raise TransientError(f"Upstox request failed: {type(exc).__name__}") from exc

    def get(self, url: str, *, request_key: str, auth: bool = True,
            metadata: Mapping[str, Any] | None = None) -> RawCapture:
        response = self._request(url, auth=auth)
        used = self._token
        if response.status_code == 401 and auth and self._token_now() != used:
            response.close()
            response = self._request(url, auth=auth)  # adopt a token rotated mid-flight
        status = response.status_code
        raise_for_status("Upstox", status, response.headers, request_key)
        return RawCapture(
            body=response.content, request_key=request_key, transport="http",
            fetched_at=self._clock(), source_url=url,
            content_type=response.headers.get("Content-Type", "application/json"),
            status_code=status, headers=dict(response.headers),
            metadata=dict(metadata or {}),
        )


__all__ = ["UpstoxClient", "retry_after_seconds"]
