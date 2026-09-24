from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from factorlab.api.app import app
from factorlab.api.us import USRepository, repository


def test_candle_reads_are_market_scoped_and_cursor_bound_to_filters():
    client = Mock()
    ident = uuid4()
    client.query.return_value = SimpleNamespace(column_names=["listing_id", "symbol", "trade_date", "as_of_time"],
        result_rows=[(ident, "AAPL", date(2026, 9, 4), datetime(2026, 9, 5, tzinfo=UTC)),
                     (ident, "AAPL", date(2026, 9, 3), datetime(2026, 9, 5, tzinfo=UTC))])
    repo = USRepository(client)
    kwargs = dict(symbol="AAPL", date_from=date(2026, 9, 1), date_to=date(2026, 9, 4), limit=1)
    page = repo.candles("daily", cursor=None, **kwargs)
    assert page.next_cursor
    assert client.query.call_args.kwargs["parameters"]["source"] == "schwab"
    assert "b.country_code = 'US'" in client.query.call_args.args[0]
    assert "FROM market.bars AS b FINAL" in client.query.call_args.args[0]
    assert "FINAL" in client.query.call_args.args[0]
    repo.candles("daily", cursor=page.next_cursor, **kwargs)
    assert client.query.call_args.kwargs["parameters"]["cursor_time"] == date(2026, 9, 4)
    with pytest.raises(HTTPException):
        repo.candles("1min", cursor=page.next_cursor, **kwargs)
    with pytest.raises(HTTPException):
        repo.candles("daily", cursor="garbage", **kwargs)
    import base64
    import json

    legacy = base64.urlsafe_b64encode(json.dumps([
        ["daily", "AAPL", "2026-09-01", "2026-09-04"],
        "2026-09-04", "AAPL", str(ident),
    ]).encode()).decode()
    with pytest.raises(HTTPException, match="Legacy candle cursor"):
        repo.candles("daily", cursor=legacy, **kwargs)


def test_us_repository_dependency_is_request_local(monkeypatch):
    created = []

    def create_storage(*_args):
        client = object()
        created.append(client)
        return SimpleNamespace(client=client)

    monkeypatch.setattr("factorlab.api.us.ClickHouseStorage.from_environment", create_storage)

    assert repository() is not repository()
    assert len(created) == 2


def test_private_hub_and_authenticated_api(monkeypatch):
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-us-key")
    repo = Mock()
    repo.dashboard.return_value = {"instruments": 10}
    app.dependency_overrides[repository] = lambda: repo
    try:
        client = TestClient(app)
        assert client.get("/api/v1/us/dashboard").status_code == 401
        assert client.get("/api/v1/us/dashboard", headers={"Authorization": "Bearer test-us-key"}).status_code == 200
        assert client.get("/hub/api/v1/us/dashboard").json()["instruments"] == 10
        assert client.get("/hub/api/v1/us/candles/1min?date_from=2020-01-01&date_to=2026-01-01").status_code == 422
        assert client.get("/hub/api/v1/us/candles/daily?limit=1001").status_code == 422
        assert client.get("/hub/api/v1/us/candles/daily?date_from=2026-09-05&date_to=2026-09-01").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_us_health_includes_universe_resolver():
    repo = USRepository(Mock())
    repo.source_status = Mock(side_effect=lambda source: {
        "source": source, "status": "error" if source == "universe" else "ready",
        "detail": source,
    })
    assert [item["source"] for item in repo.sources_status()] == [
        "universe", "schwab"]
    assert repo.overall_status()["source"] == "universe"


def test_dashboard_data_counts_are_scoped_to_configured_universe():
    repo = USRepository(Mock())
    repo.query = Mock(side_effect=[
        [{"active": 6000, "daily_configured": 500, "minute_configured": 250,
          "daily_with_data": 480, "minute_with_data": 240,
          "master_as_of": None, "ranking_as_of": None}],
        [{"actual": 0}], [{"actual": 0}],
    ])
    repo.sources_status = Mock(return_value=[])
    repo.overall_status = Mock(return_value={"source": "universe", "status": "ready",
                                             "detail": "ready"})
    result = repo.dashboard(date(2026, 9, 20))
    assert result["universe"]["active"] == 6000
    assert result["universe"]["daily_configured"] == 500
    assert result["universe"]["daily_with_data"] == 480
    assert result["universe"]["no_daily_data"] == 20
    count_sql = repo.query.call_args_list[0].args[0]
    assert "listing_id IN (SELECT listing_id FROM meta.expected_series FINAL" in count_sql
