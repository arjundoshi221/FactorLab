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
    current, older = "20260923T100000Z-abcdef1", "20260922T100000Z-1234567"
    (tmp_path / "releases" / current).mkdir(parents=True)
    (tmp_path / "releases" / older).mkdir(parents=True)
    (tmp_path / "releases/not-a-release").mkdir()
    (tmp_path / "current-release").write_text(current)
    (tmp_path / "current-image").write_text("tagged:latest")
    (tmp_path / "releases" / current / "activated-at").write_text("2026-09-23T10:00:00Z")
    (tmp_path / "releases" / current / "release.env").write_text(
        f"release_id={current}\ncommit={'c' * 40}\nimage=ghcr.io/o/f@sha256:{'d' * 64}\n"
        f"previous_release={older}\nprevious_image=unknown\nTOKEN=secret-value\n"
    )
    images = [
        {"Id": "sha256:one", "RepoTags": ["tagged:latest"], "RepoDigests": ["tagged@sha256:abc"], "Size": 100, "Created": "2026-09-20T00:00:00Z",
         "Os": "linux", "Architecture": "amd64",
         "Config": {"Env": ["IMAGE_SECRET=abc"], "Labels": {"org.opencontainers.image.revision": "c" * 40, "private.label": "hidden"}}},
        {"Id": "sha256:two", "RepoTags": None, "RepoDigests": None, "Size": 200, "Created": "2026-09-21T00:00:00Z"},
    ]
    containers = [{"Image": "sha256:one", "Name": "/api", "State": {"Status": "running", "StartedAt": "2026-09-23T10:01:00Z"},
                   "Config": {"Env": ["SECRET=abc"], "Image": "tagged:latest", "Labels": {"com.docker.compose.service": "api", "com.docker.compose.config-hash": "x"}},
                   "Mounts": ["private"]}]

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
    assert result["images"][0]["containers"] == [{
        "name": "api", "status": "running", "started_at": "2026-09-23T10:01:00Z",
        "service": "api", "image_ref": "tagged:latest",
    }]
    assert result["images"][0]["release_activated_at"] == "2026-09-23T10:00:00Z"
    assert result["images"][0]["current_release"] is True
    assert result["images"][0]["platform"] == "linux/amd64"
    assert result["images"][0]["labels"] == {"revision": "c" * 40}
    assert result["images"][1]["tags"] == []
    assert result["images"][1]["containers"] == []
    assert result["images"][1]["current_release"] is False
    assert result["release"]["commit"] == "c" * 40
    assert result["release"]["previous_release"] == older
    assert [item["id"] for item in result["releases"]] == [current, older]
    assert result["releases"][1]["activated_at"] is None
    serialized = json.dumps(result)
    for private in ("SECRET", "Mounts", "secret-value", "TOKEN", "private.label", "config-hash"):
        assert private not in serialized


def test_api_accepts_enriched_and_legacy_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    monkeypatch.setenv("FACTORLAB_DOCKER_IMAGES_SNAPSHOT", str(path))
    monkeypatch.setenv("FACTORLAB_RELEASE_ID", "20260925T050000Z-abcdef1")
    monkeypatch.delenv("FACTORLAB_COMMIT", raising=False)
    now = datetime.now(UTC).isoformat()
    legacy_image = {"id": "sha256:one", "tags": [], "digests": [], "size_bytes": 1, "created_at": now,
                    "containers": [{"name": "api", "status": "running", "started_at": now}],
                    "last_container_start_at": now, "release_activated_at": None}
    path.write_text(json.dumps({"snapshot_at": now, "release_id": None, "images": [legacy_image]}))
    client = TestClient(app)
    legacy = client.get("/hub/api/v1/docker-images").json()
    assert legacy["releases"] == [] and legacy["release"] is None
    assert legacy["images"][0]["labels"]["revision"] is None
    assert legacy["api_build"] == {"release_id": "20260925T050000Z-abcdef1", "commit": None}

    enriched_image = {**legacy_image, "current_release": True, "platform": "linux/amd64",
                      "labels": {"revision": "c" * 40},
                      "containers": [{"name": "api", "status": "running", "started_at": now, "service": "api", "image_ref": "x"}]}
    release = {"id": "r1", "commit": "c" * 40, "image": "x", "previous_release": "unknown",
               "previous_image": "unknown", "activated_at": now}
    path.write_text(json.dumps({"snapshot_at": now, "release_id": "r1", "images": [enriched_image],
                                "release": release, "releases": [release]}))
    enriched = client.get("/hub/api/v1/docker-images").json()
    assert enriched["release"]["commit"] == "c" * 40
    assert enriched["images"][0]["containers"][0]["service"] == "api"


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
