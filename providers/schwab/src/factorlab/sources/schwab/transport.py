"""Schwab market-data transport for the dataset sources.

Separate from ``market.MarketClient`` (which the production US daemon still
uses) so the new adapter never changes the legacy path. The access token and
its ``.expires_at`` companion come from ``get_secret``; the Cloudflare secrets
agent (05) keeps them fresh, so an expired or missing token is ``AuthRequired``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import requests

from factorlab.core.secrets import get_secret
from factorlab.ingest.errors import AuthRequired, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.ingest.transport import raise_for_status
from factorlab.sources.schwab.settings import SchwabSettings

MARKET_DATA_PATH = "/marketdata/v1"


class SchwabTransport:
    def __init__(self, settings: SchwabSettings, *, session: requests.Session | None = None,
                 limiter: SlidingWindowLimiter | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 secret: Callable[[str, str], str | None] = get_secret) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.limiter = limiter or SlidingWindowLimiter(settings.rate_limits.windows())
        self._clock = clock
        self._secret = secret

    def _token(self) -> str:
        name = self.settings.api.access_token_env
        token = (self._secret(name, "") or "").strip()
        expiry = (self._secret(f"{name}.expires_at", "") or "").strip()
        if not token or not expiry:
            raise AuthRequired(f"{name} or its expiry is missing; reauthenticate Schwab")
        try:
            expires = datetime.fromisoformat(expiry)
        except ValueError as exc:
            raise AuthRequired(f"{name}.expires_at is not an ISO timestamp") from exc
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= self._clock():
            raise AuthRequired(f"{name} expired at {expiry}; reauthenticate Schwab")
        return token

    def get(self, endpoint: str, params: Mapping[str, Any], *, request_key: str,
            metadata: Mapping[str, Any] | None = None) -> RawCapture:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token()}"}
        url = f"{self.settings.api.base_url.rstrip('/')}{MARKET_DATA_PATH}{endpoint}"
        self.limiter.acquire()
        try:
            response = self.session.get(url, params=dict(params), headers=headers,
                                        timeout=self.settings.api.timeout)
        except requests.RequestException as exc:
            raise TransientError(f"Schwab request failed: {type(exc).__name__}") from exc
        raise_for_status("Schwab", response.status_code, response.headers, request_key)
        return RawCapture(
            body=response.content, request_key=request_key, transport="http",
            fetched_at=self._clock(),
            source_url=f"{url}?{urlencode(sorted((k, str(v)) for k, v in params.items()))}",
            content_type=response.headers.get("Content-Type", "application/json"),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"},
            metadata={"params": {k: str(v) for k, v in params.items()}, **dict(metadata or {})},
        )


__all__ = ["MARKET_DATA_PATH", "SchwabTransport"]
