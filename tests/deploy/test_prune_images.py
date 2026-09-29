"""deploy/scripts/prune-images.py: keep what a rollback needs, remove the rest."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "prune_images", REPO / "deploy/scripts/prune-images.py"
)
prune = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prune
spec.loader.exec_module(prune)

API = "ghcr.io/arjundoshi221/factorlab-api"
MONO = "ghcr.io/arjundoshi221/factorlab"


def image(n: int, repo: str = API, tag: bool = True) -> dict:
    digest = f"{repo}@sha256:{str(n) * 64}"
    return {
        "Id": f"sha256:{n:064d}",
        "Created": f"2026-09-{n:02d}T00:00:00Z",
        "RepoTags": [f"{repo}:1.{n}.0"] if tag else [],
        "RepoDigests": [digest],
    }


class FakeDocker:
    def __init__(self, images, used=()):
        self.images, self.used, self.removed = images, list(used), []

    def __call__(self, command):
        args = command[1:]
        if args[:2] == ["image", "ls"]:
            return subprocess.CompletedProcess(
                command, 0, "\n".join(i["Id"] for i in self.images), ""
            )
        if args[:2] == ["ps", "--all"]:
            return subprocess.CompletedProcess(
                command, 0, "\n".join(f"c{n}" for n, _ in enumerate(self.used)), ""
            )
        if args[:2] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.images), "")
        if args[0] == "inspect":
            return subprocess.CompletedProcess(
                command, 0, json.dumps([{"Image": i} for i in self.used]), ""
            )
        if args[:2] == ["image", "rm"]:
            self.removed.append(args[2])
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)


def host(tmp_path, pin: int, previous: int | None = None) -> Path:
    root = tmp_path / "opt"
    (root / "state").mkdir(parents=True)
    (root / "state" / "images.env").write_text(
        f"FACTORLAB_API_IMAGE={API}@sha256:{str(pin) * 64}\n"
    )
    if previous is not None:
        record = root / "releases" / "api" / "1.9.0"
        record.mkdir(parents=True)
        (root / "releases" / "api" / "current").write_text("1.9.0\n")
        (record / "previous.json").write_text(
            json.dumps({"image": f"{API}@sha256:{str(previous) * 64}"})
        )
    return root


def test_keeps_the_newest_three_pins_running_and_rollback_images(tmp_path):
    images = [image(n) for n in range(1, 10)]
    docker = FakeDocker(images, used=[image(2)["Id"]])
    root = host(tmp_path, pin=1, previous=3)
    prune.prune(root, run=docker, log=lambda line: None)
    removed = {int(i[-2:]) for i in docker.removed}
    # 9, 8, 7 are the newest; 1 is pinned; 2 is used by a container; 3 is the rollback target.
    assert removed == {4, 5, 6}


def test_never_touches_other_repositories(tmp_path):
    foreign = [
        {
            "Id": f"sha256:{n:064d}",
            "Created": f"2026-08-{n:02d}T00:00:00Z",
            "RepoTags": [f"clickhouse/clickhouse-server:24.{n}"],
            "RepoDigests": [],
        }
        for n in range(1, 8)
    ]
    docker = FakeDocker(foreign)
    prune.prune(host(tmp_path, pin=1), run=docker, log=lambda line: None)
    assert docker.removed == []


def test_repositories_are_counted_separately_and_dry_run_removes_nothing(tmp_path):
    images = [image(n, MONO) for n in range(1, 6)] + [image(n) for n in range(6, 9)]
    docker = FakeDocker(images)
    lines = []
    prune.prune(host(tmp_path, pin=8), run=docker, dry_run=True, log=lines.append)
    assert docker.removed == []
    assert [line for line in lines if line.startswith("would remove")] == [
        f"would remove {MONO}:1.2.0",
        f"would remove {MONO}:1.1.0",
    ]


def test_unavailable_docker_prunes_nothing(tmp_path):
    def broken(command):
        return subprocess.CompletedProcess(command, 1, "", "Cannot connect")

    assert prune.prune(host(tmp_path, pin=1), run=broken, log=lambda line: None) == 1
