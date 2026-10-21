"""Schwab market-data transport for the dataset sources.

Requests are spread over a pool of accounts (``api.accounts``), each a separate
Schwab developer app with its own token and rate budget. Every request goes to the
ready account with the most free budget. A 429 benches that account for its
``Retry-After``; a 401 on a token that did not rotate benches it until the token
changes (at most five minutes). Either way the request moves on to another
account, trying each account at most once.

Credentials are read on every request. Rejected GETs retry once when the runtime
token changes; missing or expired credentials raise AuthRequired.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests

from factorlab.core.secrets import get_secret
from factorlab.ingest.errors import AuthRequired, RateLimited, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.ingest.transport import raise_for_status, retry_after_seconds
from factorlab.sources.schwab.settings import SchwabSettings

MARKET_DATA_PATH = "/marketdata/v1"
RATE_LIMIT_BENCH_SECONDS = 60.0  # when a 429 carries no Retry-After
# A rejected token is skipped until it rotates, for at most this long.
REJECTED_BENCH_SECONDS = 300.0


@dataclass
class _Account:
    id: str
    secret_name: str
    limiter: SlidingWindowLimiter
    benched_until: datetime | None = None
    rejected_token: str | None = None


class SchwabTransport:
    def __init__(
        self,
        settings: SchwabSettings,
        *,
        session: requests.Session | None = None,
        limiter: SlidingWindowLimiter | None = None,
        limiter_factory: Callable[[], SlidingWindowLimiter] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        secret: Callable[[str, str], str | None] = get_secret,
    ) -> None:
        accounts = settings.api.accounts
        if limiter is not None and len(accounts) > 1:
            raise ValueError("a shared limiter cannot pace several Schwab accounts")
        factory = limiter_factory or (lambda: SlidingWindowLimiter(settings.rate_limits.windows()))
        self.settings = settings
        self.session = session or requests.Session()
        self.accounts = [
            _Account(account, settings.api.token_secret(account), limiter or factory())
            for account in accounts
        ]
        self._clock = clock
        self._secret = secret
        self._next = 0

    def _token(self, account: _Account, now: datetime | None = None) -> str:
        name = account.secret_name
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
        if expires <= (now or self._clock()):
            raise AuthRequired(f"{name} expired at {expiry}; reauthenticate Schwab")
        return token

    def token_ready(self, now: datetime | None = None) -> bool:
        """Check rendered credentials of any account without making a network request."""
        for account in self.accounts:
            try:
                self._token(account, now)
            except AuthRequired:
                continue
            return True
        return False

    def _bench(self, account: _Account, seconds: float, rejected_token: str | None) -> None:
        account.benched_until = self._clock() + timedelta(seconds=seconds)
        account.rejected_token = rejected_token

    def _pick(self, exclude: set[str]) -> tuple[_Account, str] | None:
        """Reserve a request on the ready account with the most free budget."""
        now = self._clock()
        ready: list[tuple[_Account, str]] = []
        problems: list[str] = []
        rate_limited: list[datetime] = []
        for account in self.accounts:
            if account.id in exclude:
                continue
            try:
                token = self._token(account, now)
            except AuthRequired as exc:
                problems.append(f"{account.id}: {exc}")
                continue
            until = account.benched_until
            if until is not None and until > now:
                if account.rejected_token is None:
                    rate_limited.append(until)
                    continue
                if token == account.rejected_token:
                    problems.append(f"{account.id}: token rejected; reauthenticate Schwab")
                    continue
            account.benched_until = account.rejected_token = None
            ready.append((account, token))
        if not ready:
            if exclude:
                return None
            if rate_limited and not problems:
                wait = (min(rate_limited) - now).total_seconds()
                raise RateLimited("Schwab rate limit on every account", retry_after=wait)
            reasons = problems + [f"{len(rate_limited)} rate limited"] * bool(rate_limited)
            raise AuthRequired("no Schwab account is usable (" + "; ".join(reasons) + ")")
        # Rotate the starting point so equally free accounts share the load.
        start = self._next % len(self.accounts)
        order = {a.id: (i - start) % len(self.accounts) for i, a in enumerate(self.accounts)}
        account, token = min(ready, key=lambda item: (item[0].limiter.delay(), order[item[0].id]))
        self._next = self.accounts.index(account) + 1
        account.limiter.acquire()
        return account, token

    def _send(
        self, url: str, params: Mapping[str, Any], headers: Mapping[str, str], token: str
    ) -> requests.Response:
        return self.session.get(
            url,
            params=dict(params),
            headers={**headers, "Authorization": f"Bearer {token}"},
            timeout=self.settings.api.timeout,
        )

    def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        request_key: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> RawCapture:
        headers = {"Accept": "application/json"}
        url = f"{self.settings.api.base_url.rstrip('/')}{MARKET_DATA_PATH}{endpoint}"
        tried: set[str] = set()
        picked = self._pick(tried)
        assert picked is not None  # an empty exclude set raises instead
        account, token = picked
        try:
            while True:
                response = self._send(url, params, headers, token)
                if response.status_code == 401:
                    try:
                        refreshed = self._token(account)
                    except AuthRequired:
                        refreshed = token
                    if refreshed != token:
                        account.limiter.acquire()
                        response.close()
                        token = refreshed
                        response = self._send(url, params, headers, token)
                    if response.status_code == 401:
                        self._bench(account, REJECTED_BENCH_SECONDS, token)
                elif response.status_code == 429:
                    wait = retry_after_seconds(response.headers, RATE_LIMIT_BENCH_SECONDS)
                    self._bench(account, wait, None)
                if response.status_code not in (401, 429):
                    break
                tried.add(account.id)
                picked = self._pick(tried)
                if picked is None:
                    break
                response.close()
                account, token = picked
        except requests.RequestException as exc:
            raise TransientError(f"Schwab request failed: {type(exc).__name__}") from exc
        raise_for_status("Schwab", response.status_code, response.headers, request_key)
        return RawCapture(
            body=response.content,
            request_key=request_key,
            transport="http",
            fetched_at=self._clock(),
            source_url=f"{url}?{urlencode(sorted((k, str(v)) for k, v in params.items()))}",
            content_type=response.headers.get("Content-Type", "application/json"),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"},
            metadata={
                "params": {k: str(v) for k, v in params.items()},
                "schwab_account": account.id,
                **dict(metadata or {}),
            },
        )


__all__ = ["MARKET_DATA_PATH", "SchwabTransport"]
