"""A relayed holdings file on local disk reads exactly like the HTTP download."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from factorlab.ingest.datasets import ReferenceRequest
from factorlab.ingest.errors import PermanentError, TransientError
from factorlab.sources.ishares.sources import (
    HoldingsFund,
    IsharesSectors,
    IsharesSettings,
    IsharesUniverse,
)

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/latest-holdings.csv"
NOW = datetime(2026, 10, 21, 14, tzinfo=UTC)


def capture_body() -> bytes:
    raw = json.loads((FIXTURES / "ref.universe_membership/iwv_holdings.capture.json").read_text())
    return base64.b64decode(raw["body_b64"])


def adapter(cls, path: str):
    fund = HoldingsFund(url=URL, path=path, minimum_constituents=10)
    return cls(IsharesSettings(funds={"r3k_iwv": fund}), clock=lambda: NOW)


def fetch(source):
    (unit,) = source.plan(ReferenceRequest(market="USA"))
    return source.fetch(unit)


def test_relayed_file_matches_the_http_download(tmp_path):
    held = tmp_path / "iwv-holdings.csv"
    held.write_bytes(capture_body())
    universe = adapter(IsharesUniverse, str(held))
    capture = fetch(universe)
    assert capture.transport == "file"
    assert capture.source_url == URL  # provenance is the iShares file, not the relay
    expected = json.loads(
        (FIXTURES / "ref.universe_membership/iwv_holdings.expected.json").read_text()
    )
    assert len(universe.normalize(capture)) == len(expected)
    sectors = adapter(IsharesSectors, str(held))
    assert {r.sector_id for r in sectors.normalize(fetch(sectors))} >= {"10", "45"}


def test_missing_relay_is_transient_and_relative_paths_are_refused(tmp_path):
    with pytest.raises(TransientError, match="has not been relayed"):
        fetch(adapter(IsharesUniverse, str(tmp_path / "absent.csv")))
    with pytest.raises(PermanentError, match="must be absolute"):
        fetch(adapter(IsharesUniverse, "iwv-holdings.csv"))
