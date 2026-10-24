"""IBKR Flex Web Service transport: request a saved Flex Query, poll for the statement.

Flex is HTTPS, separate from the Gateway socket: it needs no Gateway login or
2FA, only a Flex token (Portal -> Settings -> Flex Web Service) and the id of a
saved Activity Flex Query. Two steps (Flex Web Service v3)::

    SendRequest?t=<token>&q=<query id>&v=3  -> <ReferenceCode>, <Url>
    <Url>?t=<token>&q=<reference code>&v=3  -> the statement XML (or 1019 "in progress")

The token travels as a query parameter, so it is added only to the request
itself: ``RawCapture.source_url``, metadata and error messages never contain it.
The token can be IP-restricted in the Portal; a request from another address
fails with 1013 (``AuthRequired``).

Read-only: Flex serves reports only; nothing here can place or cancel orders.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

import requests
from defusedxml import ElementTree  # remote XML: refuse entity expansion and external refs

from factorlab.core.secrets import get_secret
from factorlab.ingest.errors import AuthRequired, PermanentError, RateLimited, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.transport import raise_for_status

FLEX_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
FLEX_VERSION = "3"
TOKEN_SECRET = "IBKR_FLEX_TOKEN"
QUERY_ID_SECRET = "IBKR_FLEX_QUERY_ID"
# IBKR rejects some default client user agents on the Flex endpoints.
USER_AGENT = "factorlab-ibkr-flex/1"

# Flex error codes (IBKR "Flex Web Service" error table).
_IN_PROGRESS = frozenset({"1019"})
_RETRYABLE = frozenset({"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1021"})
_RATE_LIMITED = frozenset({"1018"})
_AUTH = frozenset({"1011", "1012", "1013", "1015", "1016"})  # inactive/expired/IP/invalid token


@dataclass(frozen=True, slots=True)
class FlexSettings:
    base_url: str = FLEX_BASE_URL
    timeout: float = 30.0
    # Large statements take a while to generate; IBKR allows about 1 request/sec per token.
    poll_interval: float = 5.0
    poll_attempts: int = 24


@dataclass(frozen=True, slots=True)
class _Status:
    status: str
    code: str
    message: str
    reference: str
    url: str


def _status(body: bytes) -> _Status | None:
    """Parse a ``FlexStatementResponse`` envelope; ``None`` for anything else (a statement)."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise TransientError(f"IBKR Flex returned unparseable XML: {exc}") from exc
    if root.tag != "FlexStatementResponse":
        return None

    def text(name: str) -> str:
        node = root.find(name)
        return (node.text or "").strip() if node is not None else ""

    return _Status(
        text("Status"), text("ErrorCode"), text("ErrorMessage"), text("ReferenceCode"), text("Url")
    )


def _raise_for_flex(status: _Status, step: str) -> NoReturn:
    code, message = status.code, status.message or "no message"
    detail = f"IBKR Flex {step} failed ({code}): {message}"
    if code in _AUTH:
        raise AuthRequired(detail)
    if code in _RATE_LIMITED:
        raise RateLimited(detail, retry_after=60.0)
    if code in _RETRYABLE or code in _IN_PROGRESS:
        raise TransientError(detail)
    raise PermanentError(detail)


class FlexClient:
    def __init__(
        self,
        settings: FlexSettings | None = None,
        *,
        session: requests.Session | None = None,
        secret: Callable[[str, str], str | None] = get_secret,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or FlexSettings()
        self.session = session or requests.Session()
        self._secret = secret
        self._clock = clock
        self._sleep = sleep

    def _credentials(self) -> tuple[str, str]:
        token = (self._secret(TOKEN_SECRET, "") or "").strip()
        query_id = (self._secret(QUERY_ID_SECRET, "") or "").strip()
        if not token:
            raise AuthRequired(f"{TOKEN_SECRET} is missing or empty")
        if not query_id:
            raise AuthRequired(f"{QUERY_ID_SECRET} is missing or empty")
        return token, query_id

    def _get(self, url: str, token: str, q: str, step: str) -> requests.Response:
        try:
            response = self.session.get(
                url,
                params={"t": token, "q": q, "v": FLEX_VERSION},
                headers={"User-Agent": USER_AGENT},
                timeout=self.settings.timeout,
            )
        except requests.RequestException as exc:
            raise TransientError(f"IBKR Flex {step} failed: {type(exc).__name__}") from exc
        raise_for_status("IBKR Flex", response.status_code, response.headers, step)
        return response

    def fetch_statement(self) -> RawCapture:
        """Run the configured Flex Query and return its statement XML as captured."""
        token, query_id = self._credentials()
        sent = _status(
            self._get(
                f"{self.settings.base_url}/SendRequest", token, query_id, "SendRequest"
            ).content
        )
        if sent is None:
            raise PermanentError("IBKR Flex SendRequest returned a statement, not a reference")
        if sent.status != "Success" or not sent.reference:
            _raise_for_flex(sent, "SendRequest")
        url = sent.url or f"{self.settings.base_url}/GetStatement"
        last: _Status | None = None
        for attempt in range(self.settings.poll_attempts):
            if attempt:
                self._sleep(self.settings.poll_interval)
            response = self._get(url, token, sent.reference, "GetStatement")
            last = _status(response.content)
            if last is None:
                return RawCapture(
                    body=response.content,
                    request_key=f"flex_activity:{query_id}",
                    transport="http",
                    fetched_at=self._clock(),
                    source_url=f"{url}?q={sent.reference}&v={FLEX_VERSION}",
                    content_type="application/xml",
                    status_code=response.status_code,
                    headers={
                        k: v for k, v in response.headers.items() if k.lower() != "set-cookie"
                    },
                    metadata={"query_id": query_id, "reference_code": sent.reference},
                )
            if last.code not in _IN_PROGRESS:
                _raise_for_flex(last, "GetStatement")
        raise TransientError(
            f"IBKR Flex statement not ready after {self.settings.poll_attempts} polls"
            + (f" ({last.code}: {last.message})" if last else "")
        )


__all__ = ["QUERY_ID_SECRET", "TOKEN_SECRET", "FlexClient", "FlexSettings"]
