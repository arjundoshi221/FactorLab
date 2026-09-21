"""Pull the 6 unitedstates/congress-legislators YAMLs into data/political/raw/."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from factorlab.countries.us.political._client import PoliticalHTTPClient

log = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
FILES = [
    "legislators-current.yaml",
    "legislators-historical.yaml",
    "committees-current.yaml",
    "committees-historical.yaml",
    "committee-membership-current.yaml",
    "executive.yaml",
]


def fetch_all(client: PoliticalHTTPClient, *, force: bool = False) -> dict[str, list | dict]:
    """Pull all 6 YAML files. Saves them under data/political/raw/legislators/{date}/{file}.

    Returns ``{filename: parsed_yaml}``.
    """
    from datetime import date as _date
    today = _date.today().isoformat()
    out: dict[str, list | dict] = {}
    for fname in FILES:
        url = f"{BASE_URL}/{fname}"
        save_as = Path("snapshots") / today / fname
        body, _path = client.get(url, save_as=save_as, ext="yaml")
        out[fname] = yaml.safe_load(body)
        log.info("legislators: fetched %s (%d bytes)", fname, len(body))
    return out
