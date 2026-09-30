"""Deploy the remaining branch-test images without changing public edge routing."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import subprocess
import time
import urllib.request
from pathlib import Path

import ingestion_test as test


def definition(new: dict, service: str, unit: str, test_id: str) -> dict:
    result = json.loads(json.dumps(new["services"][service]))
    metadata = json.loads(
        test.run("docker", "image", "inspect", f"factorlab-test-{unit}:{test_id}")
    )[0]
    if metadata["Config"]["Labels"].get("io.factorlab.component") != unit:
        raise RuntimeError(f"Wrong image: {unit}")
    result["image"] = metadata["Id"]
    return result


def own(path: Path, uid: int, gid: int) -> None:
    # Only traverse named runtime directories, never symlink targets.
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    for directory, folders, files in os.walk(path, followlinks=False):
        for name in folders + files:
            item = Path(directory) / name
            if not item.is_symlink():
                os.chown(item, uid, gid)


def wait_healthy(name: str) -> None:
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        container = test.inspect([name])[0]
        if container["State"].get("Health", {}).get("Status") == "healthy":
            return
        if not container["State"]["Running"]:
            raise RuntimeError(f"Container exited: {name}")
        time.sleep(5)
    raise RuntimeError(f"Health timeout: {name}")


def verify(root: Path) -> None:
    candidate = json.loads((root / "remaining-candidate.json").read_text())
    for service in (*test.DAEMONS, "cloudflare-secrets-agent", "api-candidate", "web"):
        container = test.inspect([f"factorlab-{service}-1"])[0]
        if not container["State"]["Running"] or container["RestartCount"]:
            raise RuntimeError(f"Unstable service: {service}")
        if container["Image"] != candidate["services"][service]["image"]:
            raise RuntimeError(f"Wrong running image: {service}")
        expected = (
            "0:0"
            if service == "cloudflare-secrets-agent"
            else ("101" if service == "web" else "10001:10001")
        )
        if container["Config"]["User"] != expected:
            raise RuntimeError(f"Wrong container user: {service}")
    for name in (
        "factorlab-cloudflare-secrets-agent-1",
        "factorlab-api-candidate-1",
        "factorlab-web-1",
    ):
        wait_healthy(name)
    before = json.loads((root / "remaining-protected.json").read_text())
    if test.public_state(["factorlab-api-1", "factorlab-clickhouse"]) != before:
        raise RuntimeError("Primary API or ClickHouse changed")
    results = {}
    for port, paths in {
        18000: [
            "/health",
            "/hub/api/v1/overview",
            "/hub/api/v1/catalog",
            "/hub/api/v1/docker-images",
            "/hub/api/v1/india/dashboard",
            "/hub/api/v1/us/dashboard",
            "/hub/api/v1/political/dashboard",
        ],
        8080: ["/healthz", "/", "/india", "/us", "/version.json"],
    }.items():
        for path in paths:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60) as response:
                results[f"{port}{path}"] = response.status
                if response.status != 200:
                    raise RuntimeError(f"Failed HTTP check: {port}{path}")
    test.store(root / "remaining-http-results.json", results)
    print(
        "Component health, non-root users, production reads and static UI checks passed", flush=True
    )


def apply(root: Path, test_id: str) -> None:
    baseline_path = root / "remaining-before.json"
    if baseline_path.exists():
        raise RuntimeError("Remaining component test already prepared")
    live = json.loads(
        test.compose(test.DEPLOY / "compose.production.yml", "config", "--format", "json")
    )
    test.store(baseline_path, live)
    test.store(
        root / "remaining-protected.json",
        test.public_state(["factorlab-api-1", "factorlab-clickhouse"]),
    )
    newer = json.loads(
        test.compose(root / "source/deploy/compose.production.yml", "config", "--format", "json")
    )
    candidate = json.loads(json.dumps(live))
    for service, unit in {
        "cloudflare-secrets-agent": "secrets-agent",
        "bootstrap": "schema-migrator",
        "web": "web",
    }.items():
        candidate["services"][service] = definition(newer, service, unit, test_id)
    candidate["networks"]["edge"] = newer["networks"]["edge"]
    for service in test.SERVICES:
        candidate["services"][service].pop("user", None)
    api = definition(newer, "api", "api", test_id)
    api["ports"] = [
        {
            "target": 8000,
            "published": "18000",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
            "mode": "ingress",
        }
    ]
    api["environment"]["FACTORLAB_SERVICE"] = "api-candidate"
    candidate["services"]["api-candidate"] = api
    test.store(root / "remaining-candidate.json", candidate)
    test.compose(root / "remaining-candidate.json", "config", "--quiet")
    # Save ownership metadata before handing runtime paths to the component UID.
    paths = [
        Path("/var/lib/factorlab/app-data"),
        *[
            Path(f"/var/log/factorlab/{unit}")
            for unit in (
                "api",
                "web",
                "secrets-agent",
                "schema-migrator",
                "ingest-india",
                "ingest-us",
                "ingest-political",
                "ingest-broker",
            )
        ],
    ]
    ownership = []
    for path in paths:
        if path.exists():
            for item in [path, *path.rglob("*")]:
                if not item.is_symlink():
                    info = item.stat()
                    ownership.append(
                        [str(item), info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
                    )
    test.store(root / "remaining-ownership-before.json", ownership)
    # Stop writers during permission and secret-renderer transitions.
    test.compose(test.DEPLOY / "compose.production.yml", "stop", *test.DAEMONS)
    try:
        own(Path("/var/lib/factorlab/app-data"), 10001, 10001)
        for path in paths[1:]:
            uid = 0 if path.name == "secrets-agent" else (101 if path.name == "web" else 10001)
            own(path, uid, 10002)
        test.install_model(root / "remaining-candidate.json")
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "cloudflare-secrets-agent",
        )
        wait_healthy("factorlab-cloudflare-secrets-agent-1")
        print("New secret agent healthy; checking schema readiness as non-root", flush=True)
        test.compose(
            test.DEPLOY / "compose.production.yml", "run", "--rm", "--no-deps", "-T", "bootstrap"
        )
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            *test.DAEMONS,
            "api-candidate",
            "web",
        )
        time.sleep(20)
        verify(root)
    except Exception:
        test.install_model(baseline_path)
        # Old root processes can consume the new 0444 secrets and UID-owned runtime paths.
        subprocess.run(
            ["docker", "rm", "-f", "factorlab-api-candidate-1", "factorlab-web-1"],
            capture_output=True,
            check=False,
        )
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "cloudflare-secrets-agent",
            *test.DAEMONS,
        )
        raise
    (root / "remaining-applied").touch(mode=0o600)


def rollback_check(root: Path) -> None:
    current = json.loads((root / "remaining-candidate.json").read_text())
    previous = json.loads(json.dumps(current))
    baseline = json.loads((root / "remaining-before.json").read_text())
    old_api = baseline["services"]["api"]
    old_api["ports"] = current["services"]["api-candidate"]["ports"]
    previous["services"]["api-candidate"] = old_api
    previous_web = json.loads(
        test.run("docker", "image", "inspect", "factorlab-test-web:20260930-previous")
    )[0]
    previous["services"]["web"]["image"] = previous_web["Id"]
    test.store(root / "remaining-rollback-check.json", previous)
    test.compose(root / "remaining-rollback-check.json", "config", "--quiet")
    try:
        test.install_model(root / "remaining-rollback-check.json")
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "api-candidate",
            "web",
        )
        deadline = time.monotonic() + 90
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:18000/health", timeout=5) as response:
                    assert response.status == 200
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(3)
        wait_healthy("factorlab-web-1")
        states = test.inspect(["factorlab-api-candidate-1", "factorlab-web-1"])
        if states[0]["Image"] != test.inspect(["factorlab-api-1"])[0]["Image"]:
            raise RuntimeError("API did not return to the preceding monolith image")
        if states[1]["Image"] != previous_web["Id"]:
            raise RuntimeError("Web did not return to the preceding test image")
        with urllib.request.urlopen("http://127.0.0.1:8080/version.json", timeout=10) as response:
            if json.load(response)["version"] != "0.0.0-test.previous":
                raise RuntimeError("Unexpected restored web version")
        print("Private API and web image restoration passed", flush=True)
    finally:
        test.install_model(root / "remaining-candidate.json")
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "api-candidate",
            "web",
        )
        verify(root)
    (root / "remaining-rollback-checked").touch(mode=0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "verify", "rollback-check"))
    parser.add_argument("test_id")
    args = parser.parse_args()
    if os.geteuid() != 0 or not test.re.fullmatch(r"[0-9]{8}-[0-9a-f]{7,40}", args.test_id):
        parser.error("Run as root with YYYYMMDD-<commit> test id")
    root = test.BASE / args.test_id
    with Path("/var/lock/factorlab-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "apply":
            apply(root, args.test_id)
        elif args.action == "rollback-check":
            rollback_check(root)
        else:
            verify(root)


if __name__ == "__main__":
    main()
