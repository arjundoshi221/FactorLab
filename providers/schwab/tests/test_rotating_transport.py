"""Runtime token rotation retries safe reads; expiry stops network calls."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests

from factorlab.ingest.errors import AuthRequired
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.schwab.settings import SchwabSettings
from factorlab.sources.schwab.transport import SchwabTransport


def test_rotated_token_retries_a_rejected_read_once():
    tokens = iter(["old", "new"])
    http = Mock(spec=requests.Session)
    http.get.side_effect = [Mock(status_code=401), Mock(status_code=200, content=b"{}", headers={})]
    transport = SchwabTransport(
        SchwabSettings(),
        session=http,
        secret=lambda name, _: (
            "2027-01-01T00:00:00Z" if name.endswith(".expires_at") else next(tokens)
        ),
        clock=lambda: datetime(2026, 10, 1, tzinfo=UTC),
        limiter=SlidingWindowLimiter([(1, 1000)]),
    )
    capture = transport.get("/quotes", {}, request_key="rotation")
    assert capture.body == b"{}"
    assert [call.kwargs["headers"]["Authorization"] for call in http.get.call_args_list] == [
        "Bearer old",
        "Bearer new",
    ]


def test_expiry_prevents_http_and_readiness_reports_auth_required():
    http = Mock(spec=requests.Session)
    transport = SchwabTransport(
        SchwabSettings(),
        session=http,
        secret=lambda name, _: "2026-09-30T00:00:00Z" if name.endswith(".expires_at") else "token",
        clock=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert not transport.token_ready()
    with pytest.raises(AuthRequired):
        transport.get("/quotes", {}, request_key="expired")
    http.get.assert_not_called()
