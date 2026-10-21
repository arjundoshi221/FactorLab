"""Pooled Schwab accounts share load and fail over on throttling or rejection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from factorlab.ingest.errors import AuthRequired, RateLimited
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.schwab.settings import SchwabApi, SchwabSettings
from factorlab.sources.schwab.transport import SchwabTransport

NOW = datetime(2026, 10, 1, tzinfo=UTC)
VALID = "2027-01-01T00:00:00Z"


def ok() -> Mock:
    return Mock(status_code=200, content=b"{}", headers={})


def pool(secrets: dict[str, str], responses: list[Mock], *, clock=lambda: NOW):
    http = Mock(spec=requests.Session)
    http.get.side_effect = responses
    transport = SchwabTransport(
        SchwabSettings(api=SchwabApi(accounts=("primary", "acct2"))),
        session=http,
        secret=lambda name, _: secrets.get(name),
        clock=clock,
        limiter_factory=lambda: SlidingWindowLimiter([(1, 1000)]),
    )
    return transport, http


def tokens(primary: str | None = "p", acct2: str | None = "a") -> dict[str, str]:
    secrets: dict[str, str] = {}
    for name, token in (("SCHWAB_ACCESS_TOKEN", primary), ("SCHWAB_ACCESS_TOKEN_ACCT2", acct2)):
        if token is not None:
            secrets[name] = token
            secrets[f"{name}.expires_at"] = VALID
    return secrets


def sent(http: Mock) -> list[str]:
    return [call.kwargs["headers"]["Authorization"] for call in http.get.call_args_list]


def test_requests_alternate_across_accounts_and_record_the_account():
    transport, http = pool(tokens(), [ok(), ok(), ok()])
    accounts = [transport.get("/quotes", {}, request_key=str(i)).metadata for i in range(3)]
    assert sent(http) == ["Bearer p", "Bearer a", "Bearer p"]
    assert [m["schwab_account"] for m in accounts] == ["primary", "acct2", "primary"]


def test_429_benches_the_account_and_moves_to_the_next():
    throttled = Mock(status_code=429, headers={"Retry-After": "30"})
    transport, http = pool(tokens(), [throttled, ok(), ok()])
    assert transport.get("/quotes", {}, request_key="k").metadata["schwab_account"] == "acct2"
    transport.get("/quotes", {}, request_key="k2")
    assert sent(http) == ["Bearer p", "Bearer a", "Bearer a"]


def test_every_account_throttled_raises_rate_limited_without_http():
    now = [NOW]
    throttled = [Mock(status_code=429, headers={"Retry-After": "30"}) for _ in range(2)]
    transport, http = pool(tokens(), throttled, clock=lambda: now[0])
    with pytest.raises(RateLimited):
        transport.get("/quotes", {}, request_key="k")
    with pytest.raises(RateLimited) as caught:
        transport.get("/quotes", {}, request_key="k2")
    assert caught.value.retry_after == 30
    assert http.get.call_count == 2
    now[0] = NOW + timedelta(seconds=31)
    http.get.side_effect = [ok()]
    transport.get("/quotes", {}, request_key="k3")


def test_unrotated_401_fails_over_and_skips_the_rejected_token():
    transport, http = pool(tokens(), [Mock(status_code=401), ok(), ok()])
    assert transport.get("/quotes", {}, request_key="k").metadata["schwab_account"] == "acct2"
    transport.get("/quotes", {}, request_key="k2")
    assert sent(http) == ["Bearer p", "Bearer a", "Bearer a"]


def test_expired_account_is_skipped_and_none_ready_raises_before_http():
    secrets = tokens()
    secrets["SCHWAB_ACCESS_TOKEN.expires_at"] = "2026-09-30T00:00:00Z"
    transport, http = pool(secrets, [ok()])
    assert transport.token_ready()
    assert transport.get("/quotes", {}, request_key="k").metadata["schwab_account"] == "acct2"

    transport, http = pool(tokens(None, None), [])
    assert not transport.token_ready()
    with pytest.raises(AuthRequired):
        transport.get("/quotes", {}, request_key="k")
    http.get.assert_not_called()


def test_account_ids_map_to_secret_names_and_reject_paths():
    api = SchwabApi(accounts=("primary", "acct2"))
    assert api.token_secret("primary") == "SCHWAB_ACCESS_TOKEN"
    assert api.token_secret("acct2") == "SCHWAB_ACCESS_TOKEN_ACCT2"
    with pytest.raises(ValueError):
        SchwabApi(accounts=("../x",))
    with pytest.raises(ValueError):
        SchwabTransport(SchwabSettings(api=api), limiter=SlidingWindowLimiter([(1, 1)]))
