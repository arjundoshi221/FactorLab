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
