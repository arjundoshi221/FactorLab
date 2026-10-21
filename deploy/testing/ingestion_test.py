"""Owner-authorized production ingestion test, outside the main release path.

Run on the VPS as root. Source and built images must already be present under
/opt/factorlab/deployment-tests/<test-id>. No migrations or image pruning.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

DEPLOY = Path("/opt/factorlab/deploy")
BASE = Path("/opt/factorlab/deployment-tests")
SERVICES = {
    "ingest-india": "ingest-india",
    "ingest-us": "ingest-us",
    "universe-us": "ingest-us",
    "ibkr-snapshot": "ingest-broker",
    "ingest-political": "ingest-political",
}
ALL_SERVICES = dict(SERVICES)
# Service-only scopes: add one new service and leave the unit's running services alone
# (they join the protected list). The service may be absent from the live model.
SERVICE_SCOPES = {"futures-us": "ingest-us", "indexes-us": "ingest-us"}
DAEMONS = [name for name in SERVICES if name != "ingest-political"]
PROTECTED = [
    "factorlab-api-1",
    "factorlab-api-candidate-1",
    "factorlab-web-1",
    "factorlab-monitor-1",
    "factorlab-clickhouse",
    "factorlab-cloudflare-secrets-agent-1",
]
BASE_PROTECTED = list(PROTECTED)


def configure_scope(root: Path, component: str | None, *, planning: bool) -> None:
    """Persist the selected unit so apply/verify/restore cannot broaden a plan."""
    scope_path = root / "scope.json"
    if scope_path.exists():
        saved = json.loads(scope_path.read_text())["component"]
        if component is not None and component != saved:
            raise RuntimeError("Component differs from the saved deployment scope")
        component = saved
    elif not planning and component is not None:
        raise RuntimeError("Cannot narrow an existing unscoped deployment plan")
    if component is not None and component not in {
        "ingest-india",
        "ingest-us",
        "ingest-broker",
        *SERVICE_SCOPES,
    }:
        raise RuntimeError("Unknown ingestion component")
    SERVICES.clear()
    if component in SERVICE_SCOPES:
        SERVICES[component] = SERVICE_SCOPES[component]
    else:
        SERVICES.update(
            {
                name: unit
                for name, unit in ALL_SERVICES.items()
                if component is None or unit == component
            }
        )
    DAEMONS[:] = [name for name in SERVICES if name != "ingest-political"]
    PROTECTED[:] = BASE_PROTECTED + [
        f"factorlab-{name}-1"
        for name in ALL_SERVICES
        if name not in SERVICES and name != "ingest-political"
    ]
    if planning and not scope_path.exists():
        store(scope_path, {"component": component})


def run(*args: str, capture: bool = True) -> str:
    result = subprocess.run(args, text=True, capture_output=capture, check=False)
    if result.returncode:
        # Compose diagnostics can contain resolved configuration. Save privately.
        if capture:
            diagnostic = BASE / "last-command-error.log"
            diagnostic.write_text((result.stdout or "") + (result.stderr or ""))
            diagnostic.chmod(0o600)
        raise RuntimeError(f"{args[0]} exited {result.returncode}; see private error log")
    return result.stdout or ""


def compose(path: Path, *args: str) -> str:
    return run(
        "docker",
        "compose",
        "-p",
        "factorlab",
        "--project-directory",
        str(DEPLOY),
        "--env-file",
        str(DEPLOY / "production.env"),
        "--profile",
        "*",
        "-f",
        str(path),
        *args,
    )


def store(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def inspect(names: list[str]) -> list[dict]:
    return json.loads(run("docker", "inspect", *names)) if names else []


def public_state(names: list[str]) -> list[dict]:
    return [
        {
            "name": c["Name"],
            "id": c["Id"],
            "image": c["Image"],
            "started": c["State"]["StartedAt"],
            "running": c["State"]["Running"],
            "restarts": c["RestartCount"],
        }
        for c in inspect(names)
    ]


def live(model: dict) -> list[str]:
    """Scoped daemons the given (live or rollback) model already defines."""
    return [name for name in DAEMONS if name in model["services"]]


def verify_protected(before: list[dict]) -> None:
    if public_state(PROTECTED) != before:
        raise RuntimeError("A protected API/database/secret-agent container changed")
    for path in ("/health", "/hub/api/v1/overview"):
        with urllib.request.urlopen("http://127.0.0.1:18000" + path, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"API check failed: {path}")


def plan(root: Path, test_id: str, *, image_test_id: str | None = None) -> None:
    image_test_id = image_test_id or test_id
    candidate_path = root / "candidate.compose.json"
    if candidate_path.exists():
        raise RuntimeError("Plan already exists; use its saved candidate")
    original = json.loads(compose(DEPLOY / "compose.production.yml", "config", "--format", "json"))
    running = public_state([f"factorlab-{name}-1" for name in live(original)])
    protected = public_state(PROTECTED)
    store(root / "before-containers.json", running)
    store(root / "protected-containers.json", protected)
    # Pin rollback to the actual running images, even if an operator's env pin drifted.
    for c in running:
        name = c["name"].removeprefix("/factorlab-").removesuffix("-1")
        original["services"][name]["image"] = c["image"]
    store(root / "rollback.compose.json", original)
    newer = json.loads(
        compose(root / "source/deploy/compose.production.yml", "config", "--format", "json")
    )
    candidate = json.loads(json.dumps(original))
    for name, unit in SERVICES.items():
        definition = newer["services"][name]
        # Keep operator-specific provider and runtime configuration from the live model.
        definition["environment"] = {
            **definition.get("environment", {}),
            **original["services"].get(name, {}).get("environment", {}),
        }
        image = f"factorlab-test-{unit}:{image_test_id}"
        metadata = json.loads(run("docker", "image", "inspect", image))[0]
        if metadata["Config"]["Labels"].get("io.factorlab.component") != unit:
            raise RuntimeError(f"Wrong image component: {unit}")
        if (
            not metadata["Config"]["Labels"]
            .get("org.opencontainers.image.revision", "")
            .startswith(test_id.split("-", 1)[1])
        ):
            raise RuntimeError(f"Image revision does not match the test id: {unit}")
        # Keep the already verified secret/data ownership contract. The remaining
        # component rollout installed UID 10001; do not revert it to root.
        definition["user"] = (
            original["services"].get(name, {}).get("user") or metadata["Config"]["User"]
        )
        definition["image"] = metadata["Id"]
        candidate["services"][name] = definition
        for mount in definition.get("volumes", []):
            if mount["type"] == "bind" and mount["source"].startswith("/var/log/factorlab/"):
                Path(mount["source"]).mkdir(parents=True, exist_ok=True, mode=0o750)
    store(candidate_path, candidate)
    compose(candidate_path, "config", "--quiet")
    # Import every production subcommand before stopping any live process.
    for unit, subcommands in {
        "ingest-india": ["daemon", "premarket", "engine"],
        "ingest-us": ["daemon", "universe", "futures", "indexes", "engine"],
        "ingest-broker": ["snapshot", "engine"],
        "ingest-political": ["bootstrap", "engine"],
    }.items():
        if unit not in SERVICES.values():
            continue
        image = f"factorlab-test-{unit}:{image_test_id}"
        for command in subcommands:
            run("docker", "run", "--rm", "--network", "none", image, command, "--help")
        run("docker", "run", "--rm", "--network", "none", image, "engine", "validate")
        print(f"smoke imports passed: {unit}", flush=True)
    verify_protected(protected)
    (root / "plan-ready").touch(mode=0o600)
    print("Plan validated; production containers still unchanged", flush=True)


def install_model(model: Path) -> None:
    temporary = DEPLOY / "compose.ingestion-test.tmp"
    temporary.write_bytes(model.read_bytes())
    temporary.chmod(0o600)
    temporary.replace(DEPLOY / "compose.production.yml")


def verify(root: Path) -> None:
    candidate = json.loads((root / "candidate.compose.json").read_text())
    states = inspect([f"factorlab-{name}-1" for name in DAEMONS])
    for c, service in zip(states, DAEMONS, strict=True):
        if not c["State"]["Running"] or c["RestartCount"]:
            raise RuntimeError(f"Unstable service: {service}")
        if c["Image"] != candidate["services"][service]["image"]:
            raise RuntimeError(f"Image mismatch: {service}")
    verify_protected(json.loads((root / "protected-containers.json").read_text()))
    store(root / "after-containers.json", public_state([f"factorlab-{name}-1" for name in DAEMONS]))
    print("New images running; API, ClickHouse and secret agent identities unchanged", flush=True)


def restore(root: Path) -> None:
    rollback = json.loads((root / "rollback.compose.json").read_text())
    # Services the trial added have no preceding definition: remove their containers.
    for name in DAEMONS:
        if name not in rollback["services"]:
            subprocess.run(
                ("docker", "rm", "-f", f"factorlab-{name}-1"), capture_output=True, check=False
            )
    install_model(root / "rollback.compose.json")
    if previous := live(rollback):
        compose(
            DEPLOY / "compose.production.yml",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            *previous,
        )
    (root / "restored").touch(mode=0o600)
    verify_protected(json.loads((root / "protected-containers.json").read_text()))
    print("Previous ingestion images restored; no data or schema rollback performed", flush=True)


def apply(root: Path) -> None:
    if not (root / "plan-ready").exists() or (root / "applied").exists():
        raise RuntimeError("Need a validated, unapplied plan")
    previous = live(json.loads((root / "rollback.compose.json").read_text()))
    if public_state([f"factorlab-{name}-1" for name in previous]) != json.loads(
        (root / "before-containers.json").read_text()
    ):
        raise RuntimeError("Production ingestion changed since plan; refusing stale replacement")
    verify_protected(json.loads((root / "protected-containers.json").read_text()))
    try:
        # Stop old writers first. --no-deps prevents recreating shared infrastructure.
        if previous:
            compose(DEPLOY / "compose.production.yml", "stop", *previous)
        install_model(root / "candidate.compose.json")
        compose(
            DEPLOY / "compose.production.yml", "up", "-d", "--no-deps", "--force-recreate", *DAEMONS
        )
        print("Waiting 30 seconds for startup stability", flush=True)
        time.sleep(30)
        verify(root)
    except Exception:
        restore(root)
        raise
    (root / "applied").touch(mode=0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "verify", "restore"))
    parser.add_argument("test_id")
    parser.add_argument(
        "--component", choices=("ingest-india", "ingest-us", "ingest-broker", *SERVICE_SCOPES)
    )
    parser.add_argument("--image-test-id", help="Reuse built images from this earlier test id")
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r"[0-9]{8}-[0-9a-f]{7,40}", args.test_id):
        parser.error("Run as root with YYYYMMDD-<commit> test id")
    if args.image_test_id and (
        args.action != "plan" or not re.fullmatch(r"[0-9]{8}-[0-9a-f]{7,40}", args.image_test_id)
    ):
        parser.error("--image-test-id is only valid for plan with YYYYMMDD-<commit>")
    root = BASE / args.test_id
    if not (root / "backup/deploy/compose.production.yml").is_file():
        parser.error("Missing private backup of the live deployment")
    with Path("/var/lock/factorlab-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configure_scope(root, args.component, planning=args.action == "plan")
        if args.action == "plan":
            plan(root, args.test_id, image_test_id=args.image_test_id)
        elif args.action == "apply":
            apply(root)
        elif args.action == "verify":
            verify(root)
        else:
            restore(root)


if __name__ == "__main__":
    main()
