"""Replace only the public branch-trial API candidate and web containers.

This runner is installed by the UI trial workflow. Resolved Compose models remain
root-only on the host; no production schema or ingestion service is changed.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path

import ingestion_test as test

SERVICES = {"api-candidate": "api", "web": "web"}
IMAGE_RE = re.compile(r"ghcr\.io/arjundoshi221/factorlab-(api|web)@sha256:[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
TRIAL_RE = re.compile(r"ui-[0-9a-f]{12}\Z")


def candidate_model(live: dict, image_ids: dict[str, str]) -> dict:
    missing = SERVICES.keys() - live["services"].keys()
    if missing:
        raise RuntimeError(f"Trial services are missing: {', '.join(sorted(missing))}")
    candidate = copy.deepcopy(live)
    for service, image_id in image_ids.items():
        if service not in SERVICES or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError(f"Invalid candidate image for {service}")
        candidate["services"][service]["image"] = image_id
    if set(image_ids) != set(SERVICES):
        raise RuntimeError("Both API candidate and web images are required")
    return candidate


def running_names() -> list[str]:
    return test.run(
        "docker",
        "ps",
        "--filter",
        "label=com.docker.compose.project=factorlab",
        "--format",
        "{{.Names}}",
    ).splitlines()


def protected_names() -> list[str]:
    changed = {f"factorlab-{service}-1" for service in SERVICES}
    names = sorted(set(running_names()) - changed)
    if not {"factorlab-api-1", "factorlab-clickhouse"}.issubset(names):
        raise RuntimeError("Primary API or ClickHouse is not running")
    return names


def image_id(image: str, unit: str, commit: str, version: str) -> str:
    if not IMAGE_RE.fullmatch(image) or IMAGE_RE.fullmatch(image).group(1) != unit:
        raise RuntimeError(f"Invalid {unit} image reference")
    test.run("docker", "pull", image)
    [metadata] = json.loads(test.run("docker", "image", "inspect", image))
    labels = metadata["Config"].get("Labels") or {}
    if (
        labels.get("io.factorlab.component") != unit
        or labels.get("org.opencontainers.image.revision") != commit
        or labels.get("org.opencontainers.image.version") != version
    ):
        raise RuntimeError(f"{unit} image labels do not match the trial commit")
    if metadata["Config"].get("User", "").split(":")[0] != ("101" if unit == "web" else "10001"):
        raise RuntimeError(f"Wrong runtime user in {unit} image")
    return metadata["Id"]


def wait_healthy(service: str) -> None:
    deadline = time.monotonic() + 180
    name = f"factorlab-{service}-1"
    while time.monotonic() < deadline:
        [container] = test.inspect([name])
        if not container["State"]["Running"] or container["RestartCount"]:
            raise RuntimeError(f"Unstable service: {service}")
        if container["State"].get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(3)
    raise RuntimeError(f"Health timeout: {service}")


def read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP check failed: {url}")
        return json.load(response)


def verify(root: Path) -> None:
    candidate = json.loads((root / "candidate.json").read_text())
    for service in SERVICES:
        wait_healthy(service)
        [container] = test.inspect([f"factorlab-{service}-1"])
        if container["Image"] != candidate["services"][service]["image"]:
            raise RuntimeError(f"Wrong image running: {service}")
    protected = json.loads((root / "protected.json").read_text())
    if test.public_state([row["name"].lstrip("/") for row in protected]) != protected:
        raise RuntimeError("A protected production container changed")
    version = read_json("http://127.0.0.1:8080/version.json")
    if version.get("commit") != (root / "commit").read_text().strip():
        raise RuntimeError("Web commit does not match the trial")
    for path in ("/health", "/hub/api/v1/overview"):
        with urllib.request.urlopen(f"http://127.0.0.1:18000{path}", timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"API check failed: {path}")
    for market in ("US", "IN"):
        for stream in ("daily", "historical"):
            result = read_json(
                "http://127.0.0.1:18000/hub/api/v1/market-data/universe"
                f"?market={market}&stream={stream}&limit=1"
            )
            if not isinstance(result.get("configured_total"), int) or not isinstance(
                result.get("items"), list
            ):
                raise RuntimeError(f"Invalid universe response: {market}/{stream}")
    with urllib.request.urlopen("http://127.0.0.1:8080/market-data", timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("Market Data page is unavailable")
    print("FACTORLAB_RESULT_VERIFICATION=passed", flush=True)


def restore(root: Path) -> None:
    test.install_model(root / "before.json")
    for service in SERVICES:
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            service,
        )
        wait_healthy(service)
        [container] = test.inspect([f"factorlab-{service}-1"])
        before = json.loads((root / "before.json").read_text())
        if container["Image"] != before["services"][service]["image"]:
            raise RuntimeError(f"Rollback image mismatch: {service}")
    print("FACTORLAB_RESULT_ROLLBACK=restored", flush=True)


def apply(root: Path, commit: str, api_image: str, web_image: str) -> None:
    if root.exists():
        raise RuntimeError("Trial ID already exists")
    if (test.DEPLOY.parent / "state/images.env").exists():
        raise RuntimeError("Component pins exist; use the normal release workflow")
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True)
    for filename in ("ui_test.py", "ingestion_test.py"):
        shutil.copy2(Path(__file__).with_name(filename), root / filename)
        (root / filename).chmod(0o600)
    version = f"0.0.0-trial.{commit[:12]}"
    ids = {
        "api-candidate": image_id(api_image, "api", commit, version),
        "web": image_id(web_image, "web", commit, version),
    }
    live = json.loads(
        test.compose(test.DEPLOY / "compose.production.yml", "config", "--format", "json")
    )
    names = protected_names()
    protected = test.public_state(names)
    for service in SERVICES:
        [container] = test.inspect([f"factorlab-{service}-1"])
        live["services"][service]["image"] = container["Image"]
    candidate = candidate_model(live, ids)
    test.store(root / "before.json", live)
    test.store(root / "candidate.json", candidate)
    test.store(root / "protected.json", protected)
    (root / "commit").write_text(commit + "\n")
    test.compose(root / "candidate.json", "config", "--quiet")
    try:
        test.install_model(root / "candidate.json")
        for service in SERVICES:
            test.compose(
                test.DEPLOY / "compose.production.yml",
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                service,
            )
            wait_healthy(service)
        verify(root)
    except Exception:
        restore(root)
        raise
    (root / "applied").touch(mode=0o600)
    print("FACTORLAB_RESULT_ROLLBACK=not-needed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "verify", "restore"))
    parser.add_argument("test_id")
    parser.add_argument("--commit")
    parser.add_argument("--api-image")
    parser.add_argument("--web-image")
    args = parser.parse_args()
    if os.geteuid() != 0 or not TRIAL_RE.fullmatch(args.test_id):
        raise RuntimeError("Run as root with a ui-<12 hex> trial ID")
    root = test.BASE / args.test_id
    with Path("/var/lock/factorlab-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "apply":
            if not args.commit or not COMMIT_RE.fullmatch(args.commit):
                raise RuntimeError("A full commit SHA is required")
            if args.test_id != f"ui-{args.commit[:12]}" or not args.api_image or not args.web_image:
                raise RuntimeError("Trial ID and both component images must match the commit")
            apply(root, args.commit, args.api_image, args.web_image)
        elif not root.is_dir():
            raise RuntimeError("Unknown trial ID")
        elif args.command == "verify":
            verify(root)
        else:
            restore(root)


if __name__ == "__main__":
    main()
