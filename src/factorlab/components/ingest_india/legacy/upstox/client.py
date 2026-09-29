"""Upstox authenticated HTTP session factory."""

import logging

import requests
from dotenv import find_dotenv, load_dotenv

from factorlab.core.secrets import get_secret

log = logging.getLogger(__name__)


class UpstoxSession(requests.Session):
    """Session that adopts a rotated tmpfs token before every request."""

    def __init__(self, token: str):
        super().__init__()
        self._access_token = token
        self.headers.update({
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        })

    def _reload_token(self) -> bool:
        latest = (get_secret("UPSTOX_ACCESS_TOKEN", "") or "").strip()
        if not latest or latest == self._access_token:
            return False
        self._access_token = latest
        self.headers["Authorization"] = f"Bearer {latest}"
        log.info("Adopted rotated Upstox access token")
        return True

    def request(self, method, url, *args, **kwargs):
        self._reload_token()
        response = super().request(method, url, *args, **kwargs)
        safe_to_retry = str(method).upper() in {"GET", "HEAD", "OPTIONS"}
        if response.status_code == 401 and safe_to_retry and self._reload_token():
            response.close()
            response = super().request(method, url, *args, **kwargs)
        return response


def get_session(token: str | None = None) -> UpstoxSession:
    """Return an authenticated requests.Session with Upstox headers.

    If *token* is not provided, reads UPSTOX_ACCESS_TOKEN from environment.
    """
    if token is None:
        load_dotenv(find_dotenv(usecwd=True))
        token = (get_secret("UPSTOX_ACCESS_TOKEN", "") or "").strip()
        if not token:
            raise OSError("UPSTOX_ACCESS_TOKEN is missing or empty in .env")

    return UpstoxSession(token)
