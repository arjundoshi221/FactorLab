"""EODHD transport: API-key auth, rate limiting, redacted capture URLs.

The API key travels as a query parameter, so it is added only to the request
itself: ``RawCapture.source_url`` and the capture metadata never contain it.
HTTP 402 is EODHD's "daily limit reached" and ends the run (``QuotaExhausted``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import requests

from factorlab.core.secrets import get_secret
from factorlab.shared.ingest.errors import AuthRequired, TransientError
from factorlab.shared.ingest.provider import RawCapture
from factorlab.shared.ingest.ratelimit import SlidingWindowLimiter
from factorlab.shared.ingest.transport import raise_for_status
from factorlab.sources.eodhd.settings import EodhdSettings

_QUOTA = frozenset({402})


class EodhdClient:
    def __init__(self, settings: EodhdSettings, *, session: requests.Session | None = None,
                 limiter: SlidingWindowLimiter | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 secret: Callable[[str, str], str | None] = get_secret) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.limiter = limiter or SlidingWindowLimiter(settings.rate_limits.windows())
        self._clock = clock
        self._secret = secret

    def get(self, path: str, params: Mapping[str, Any] | None = None, *, request_key: str,
            metadata: Mapping[str, Any] | None = None) -> RawCapture:
        key = (self._secret(self.settings.api.key_env, "") or "").strip()
        if not key:
            raise AuthRequired(f"{self.settings.api.key_env} is missing or empty")
        safe = {"fmt": "json", **dict(params or {})}
        url = f"{self.settings.api.base_url.rstrip('/')}{path}"
        self.limiter.acquire()
        try:
            response = self.session.get(url, params={**safe, self.settings.api.key_param: key},
                                        timeout=self.settings.api.timeout)
        except requests.RequestException as exc:
            raise TransientError(f"EODHD request failed: {type(exc).__name__}") from exc
        raise_for_status("EODHD", response.status_code, response.headers, request_key,
                         quota_statuses=_QUOTA)
        return RawCapture(
            body=response.content, request_key=request_key, transport="http",
            fetched_at=self._clock(), source_url=f"{url}?{urlencode(sorted(safe.items()))}",
            content_type=response.headers.get("Content-Type", "application/json"),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"},
            metadata={"params": safe, **dict(metadata or {})},
        )


__all__ = ["EodhdClient"]
