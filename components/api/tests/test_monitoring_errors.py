from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.monitoring import repository


def result(items):
    columns = list(items[0]) if items else []
    return SimpleNamespace(
        column_names=columns, result_rows=[tuple(item[c] for c in columns) for item in items]
    )


def test_universe_failure_remains_visible_without_service_error_packets():
    now = datetime.now(UTC)
    failed = now - timedelta(days=3)
    queries = []

    def query(sql, **kwargs):
        queries.append((sql, kwargs))
        if "status IN ('failed', 'partial')" in sql:
            return result(
                [
                    {
                        "pipeline": "us_universe_sync",
                        "source": "github_csv",
                        "status": "failed",
                        "message": "Roster validation failed password=test-secret",
                        "first_seen": failed,
                        "last_seen": failed,
                        "occurrences": 200,
                        "run_id": uuid4(),
                        "failed_series": 200,
                    }
                ]
            )
        if "AS last_success" in sql:
            return result(
                [{"pipeline": "us_universe_sync", "source": "other_provider", "last_success": now}]
            )
        if "meta.source_status" in sql:
            return result(
                [
                    {
                        "country_code": "US",
                        "source": "universe",
                        "status": "error",
                        "detail": "Universe refresh failed",
                        "checked_at": now,
                    }
                ]
            )
        return result([])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        response = TestClient(app).get(
            "/hub/api/v1/monitoring/errors", params={"service_id": "universe-us"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["unavailable"] == []
        run = next(item for item in data["issues"] if item["origin"] == "pipeline")
        assert run["service_id"] == "universe-us"
        assert run["occurrences"] == 200
        assert run["recovery"] == "unconfirmed"  # Success by another provider is not recovery.
        assert "test-secret" not in response.text
        source = next(item for item in data["issues"] if item["origin"] == "source")
        assert source["recovery"] == "source_failure"
        assert source["first_seen"] is None and source["occurrences"] is None
        run_query = next(
            kwargs for sql, kwargs in queries if "status IN ('failed', 'partial')" in sql
        )
        assert "us_universe_sync" in run_query["parameters"]["pipelines"]
    finally:
        app.dependency_overrides.clear()


def test_error_view_marks_partial_history_and_keeps_current_old_source_failures():
    now = datetime.now(UTC)

    def query(sql, **kwargs):
        if "meta.service_events" in sql:
            raise RuntimeError("private database details")
        if "meta.source_status" in sql:
            return result(
                [
                    {
                        "country_code": "US",
                        "source": "universe",
                        "status": "error",
                        "detail": "Roster not available",
                        "checked_at": now - timedelta(days=4),
                    }
                ]
            )
        return result([])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        response = TestClient(app).get("/hub/api/v1/monitoring/errors?hours=24")
        assert response.status_code == 200
        assert response.json()["unavailable"] == ["telemetry"]
        assert response.json()["issues"][0]["recovery"] == "source_stale"
        assert "private database" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_error_filters_are_validated_and_parameterized():
    queries = []

    def query(sql, **kwargs):
        queries.append((sql, kwargs))
        return result([])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        client = TestClient(app)
        response = client.get(
            "/hub/api/v1/monitoring/errors",
            params={"hours": 720, "service_id": "' OR 1=1", "severity": "warning", "limit": 5},
        )
        assert response.status_code == 200
        assert len(queries) == 2  # Warning filter does not query failed ingestion runs.
        assert "' OR 1=1" not in queries[0][0]
        assert queries[0][1]["parameters"]["service_id"] == "' OR 1=1"
        assert queries[0][1]["parameters"]["kinds"] == ["warn"]
        assert queries[0][1]["settings"]["readonly"] == 2
        for params in ({"hours": 0}, {"hours": 721}, {"severity": "unknown"}, {"limit": 201}):
            assert client.get("/hub/api/v1/monitoring/errors", params=params).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_later_success_is_evidence_but_does_not_clear_historical_failure():
    now = datetime.now(UTC)

    def query(sql, **kwargs):
        if "status IN ('failed', 'partial')" in sql:
            return result(
                [
                    {
                        "pipeline": "us_universe_sync",
                        "source": "github_csv",
                        "status": "failed",
                        "message": "Refresh failed",
                        "first_seen": now - timedelta(days=3),
                        "last_seen": now - timedelta(days=1),
                        "occurrences": 5,
                        "run_id": uuid4(),
                        "failed_series": 5,
                    }
                ]
            )
        if "AS last_success" in sql:
            return result(
                [{"pipeline": "us_universe_sync", "source": "github_csv", "last_success": now}]
            )
        return result([])

    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    try:
        response = TestClient(app).get("/hub/api/v1/monitoring/errors")
        assert response.status_code == 200
        assert response.json()["issues"][0]["recovery"] == "later_success"
        assert response.json()["issues"][0]["occurrences"] == 5
    finally:
        app.dependency_overrides.clear()
