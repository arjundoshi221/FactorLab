"""Upstox authenticated HTTP session factory."""

import logging
import os

import requests
from dotenv import find_dotenv, load_dotenv

log = logging.getLogger(__name__)


def get_session(token: str | None = None) -> requests.Session:
    """Return an authenticated requests.Session with Upstox headers.

    If *token* is not provided, reads UPSTOX_ACCESS_TOKEN from environment.
    """
    if token is None:
        load_dotenv(find_dotenv(usecwd=True))
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
        if not token:
            raise EnvironmentError("UPSTOX_ACCESS_TOKEN is missing or empty in .env")

    s = requests.Session()
    s.headers.update({
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    })
    return s
