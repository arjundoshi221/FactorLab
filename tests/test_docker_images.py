import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from factorlab.api.app import app


def collector_module():
    path = Path(__file__).resolve().parents[1] / "deploy/scripts/collect-docker-images.py"
    spec = importlib.util.spec_from_file_location("docker_image_collector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collector_includes_unused_untagged_and_running_without_private_metadata(tmp_path, monkeypatch):
    module = collector_module()
    (tmp_path / "releases/r1").mkdir(parents=True)
    (tmp_path / "current-release").write_text("r1")
    (tmp_path / "current-image").write_text("tagged:latest")
    (tmp_path / "releases/r1/activated-at").write_text("2026-09-23T10:00:00Z")
    images = [
        {"Id": "sha256:one", "RepoTags": ["tagged:latest"], "RepoDigests": ["tagged@sha256:abc"], "Size": 100, "Created": "2026-09-20T00:00:00Z"},
        {"Id": "sha256:two", "RepoTags": None, "RepoDigests": None, "Size": 200, "Created": "2026-09-21T00:00:00Z"},
    ]
    containers = [{"Image": "sha256:one", "Name": "/api", "State": {"Status": "running", "StartedAt": "2026-09-23T10:01:00Z"}, "Config": {"Env": ["SECRET=abc"]}, "Mounts": ["private"]}]

    def docker(*args):
        if args[:2] == ("image", "ls"):
            return "sha256:one\nsha256:two\n"
        if args[:2] == ("ps", "-aq"):
            return "container-one"
        if args[:2] == ("container", "inspect"):
            return json.dumps(containers)
        if args[:2] == ("image", "inspect"):
            return json.dumps(images if len(args) > 3 else [images[0]])
        raise AssertionError(args)

    monkeypatch.setattr(module, "docker", docker)
    result = module.collect(tmp_path)
    assert len(result["images"]) == 2
    assert result["images"][0]["containers"] == [{"name": "api", "status": "running", "started_at": "2026-09-23T10:01:00Z"}]
    assert result["images"][0]["release_activated_at"] == "2026-09-23T10:00:00Z"
    assert result["images"][1]["tags"] == []
    assert result["images"][1]["containers"] == []
    assert "SECRET" not in json.dumps(result)
    assert "Mounts" not in json.dumps(result)


def test_api_valid_missing_malformed_and_stale_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    monkeypatch.setenv("FACTORLAB_DOCKER_IMAGES_SNAPSHOT", str(path))
    client = TestClient(app)
    assert client.get("/hub/api/v1/docker-images").status_code == 503
    path.write_text("{")
    assert client.get("/hub/api/v1/docker-images").status_code == 503
    snapshot = {"snapshot_at": datetime.now(UTC).isoformat(), "release_id": None, "images": []}
    path.write_text(json.dumps(snapshot))
    response = client.get("/hub/api/v1/docker-images")
    assert response.status_code == 200
    assert response.json()["stale"] is False
    snapshot["images"] = [{"id": "sha256:one", "Config": {"Env": ["SECRET=abc"]}}]
    path.write_text(json.dumps(snapshot))
    assert client.get("/hub/api/v1/docker-images").status_code == 503
    snapshot["images"] = []
    snapshot["snapshot_at"] = (datetime.now(UTC) - timedelta(minutes=4)).isoformat()
    path.write_text(json.dumps(snapshot))
    assert client.get("/hub/api/v1/docker-images").json()["stale"] is True
