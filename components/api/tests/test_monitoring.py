from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.monitoring import repository, services_response


def test_stale_watcher_and_registry_join():
    now = datetime.now(UTC)
    rows = [{"service_id": "monitor", "status": "up", "updated_at": now - timedelta(seconds=16)}]
    response = services_response(rows, {"svc": {"interval_s": 30}}, now)
    assert response["monitor_offline"]
    assert next(s for s in response["services"] if s["service_id"] == "svc")["status"] == "unknown"
    rows[0]["updated_at"] = now
    assert not services_response(rows, {}, now)["monitor_offline"]


def test_events_validate_and_parameterize_filters():
    queries = []

    def query(sql, **kwargs):
        queries.append((sql, kwargs))
        return SimpleNamespace(column_names=[], result_rows=[])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        client = TestClient(app)
        result = client.get(
            "/hub/api/v1/monitoring/events",
            params={
                "service_id": "' OR 1=1",
                "type": "err",
                "since": "2026-09-30T00:00:00Z",
                "limit": 5,
            },
        )
        assert result.status_code == 200
        assert "' OR 1=1" not in queries[0][0]
        assert queries[0][1]["parameters"]["service_id"] == "' OR 1=1"
        for params in ({"limit": 1001}, {"type": "garbage"}, {"since": "2026-09-30T00:00:00"}):
            assert client.get("/hub/api/v1/monitoring/events", params=params).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_daily_and_coverage_are_bounded_read_only_aggregates():
    queries = []

    def query(sql, **kwargs):
        queries.append((sql, kwargs))
        return SimpleNamespace(column_names=[], result_rows=[])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        client = TestClient(app)
        daily = client.get("/hub/api/v1/monitoring/daily")
        assert daily.status_code == 200
        assert daily.json()["days"] == []
        sql, kwargs = queries[-1]
        assert "GROUP BY service_id, day" in sql
        assert "LIMIT" not in sql  # Counts must include all events, not the event log sample.
        assert kwargs["parameters"]["since"].hour == 0
        assert (kwargs["parameters"]["now"].date() - kwargs["parameters"]["since"].date()).days == 6
        coverage = client.get("/hub/api/v1/monitoring/coverage")
        assert coverage.status_code == 200
        assert coverage.json()["feeds"] == []
        sql, kwargs = queries[1]
        assert "meta.session_coverage FINAL" in sql
        assert "LIMIT 1 BY country_code, source, resolution" in sql
        assert (kwargs["parameters"]["today"] - kwargs["parameters"]["since"]).days == 30
        assert kwargs["settings"] == {"readonly": 2, "max_execution_time": 5}
        assert "isNull(c.listing_id)" in queries[-1][0]
        assert queries[-1][1]["settings"]["join_use_nulls"] == 1
    finally:
        app.dependency_overrides.clear()


def test_operational_panels_report_database_failures():
    def query(*args, **kwargs):
        raise RuntimeError("private connection details")

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        client = TestClient(app)
        for route in ("daily", "coverage"):
            response = client.get(f"/hub/api/v1/monitoring/{route}")
            assert response.status_code == 503
            assert "private" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_active_feed_without_any_checks_remains_visible():
    def query(sql, **kwargs):
        if "meta.expected_series" in sql:
            return SimpleNamespace(
                column_names=[
                    "country_code",
                    "source",
                    "resolution",
                    "active_series",
                    "unreported_series",
                ],
                result_rows=[("US", "schwab", "daily", 20, 20)],
            )
        return SimpleNamespace(column_names=[], result_rows=[])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        response = TestClient(app).get("/hub/api/v1/monitoring/coverage")
        assert response.status_code == 200
        feed = response.json()["feeds"][0]
        assert feed["trade_date"] is None
        assert feed["checked_at"] is None
        assert feed["active_series"] == feed["unreported_series"] == 20
        assert feed["expected"] == 0  # No bar count is invented for absent checks.
    finally:
        app.dependency_overrides.clear()
