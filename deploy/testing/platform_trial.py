"""Install a branch-trial platform image (monitor, secrets agent or schema migrator).

Installed by the platform trial workflow (branch-platform-deploy.yml). Under the release
lock it:

1. creates the private trial directory, backs up the live deployment directory and
   installs the candidate Compose model the workflow transferred;
2. pulls the image by digest and checks its labels and runtime user;
3. plans: pins the rollback model to the running image, builds the candidate model
   (the new service definition, keeping the live environment), validates it and
   imports the component offline;
4. applies one scope:

   monitor          recreates ``monitor``, then waits for health and for the API to
                    report the monitor online
   secrets-agent    recreates ``cloudflare-secrets-agent``, then waits for health and a
                    ``.agent-ready`` newer than before (written by the new container).
                    Consumers keep their tmpfs secrets while it restarts.
   schema-migrator  runs the new ``bootstrap`` readiness check and ``migrate status``
                    (both read-only, no DDL), then pins the image for later starts

   and checks that ClickHouse, the API, web and the other platform container did not
   change. A failure restores the preceding model and image.

Schema migrations are never applied here.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import ingest_trial as trial
import ingestion_test as test


@dataclass(frozen=True)
class Scope:
    unit: str
    service: str
    user: str
    module: str


SCOPES = {
    "monitor": Scope("monitor", "monitor", "10001", "factorlab.components.monitor"),
    "secrets-agent": Scope(
        "secrets-agent", "cloudflare-secrets-agent", "0", "factorlab.components.secrets_agent"
    ),
    "schema-migrator": Scope(
        "schema-migrator", "bootstrap", "10001", "factorlab.components.schema_migrator"
    ),
}
RUNNERS = ("platform_trial.py", "ingest_trial.py", "ingestion_test.py")
PROTECTED = (
    "factorlab-clickhouse",
    "factorlab-api-1",
    "factorlab-api-candidate-1",
    "factorlab-web-1",
    "factorlab-monitor-1",
    "factorlab-cloudflare-secrets-agent-1",
)
API = "http://127.0.0.1:18000"
AGENT_READY = "/run/secrets/clickhouse/.agent-ready"
HEALTH_TIMEOUT = 180
TRIAL_RE = re.compile(r"[0-9]{8}(?:[0-9]{6})?-[0-9a-f]{12}\Z")


def container(service: str) -> str:
    return f"factorlab-{service}-1"


def existing(names: list[str]) -> list[str]:
    present = set(test.run("docker", "ps", "-a", "--format", "{{.Names}}").split())
    return [name for name in names if name in present]


def protected(scope: Scope) -> list[str]:
    return existing([name for name in PROTECTED if name != container(scope.service)])


def check_protected(root: Path, scope: Scope) -> None:
    if test.public_state(protected(scope)) != json.loads(
        (root / "protected-containers.json").read_text()
    ):
        raise RuntimeError("A protected ClickHouse/API/web/platform container changed")
    with urllib.request.urlopen(API + "/health", timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("API health check failed")


def plan(root: Path, scope: Scope, image_id: str) -> None:
    live = json.loads(
        test.compose(test.DEPLOY / "compose.production.yml", "config", "--format", "json")
    )
    if scope.service not in live["services"]:
        raise RuntimeError(f"{scope.service} is not in the live model")
    before = test.public_state(existing([container(scope.service)]))
    # Pin rollback to the actual running image, even if an operator's env pin drifted.
    for state in before:
        live["services"][scope.service]["image"] = state["image"]
    test.store(root / "before-container.json", before)
    test.store(root / "protected-containers.json", test.public_state(protected(scope)))
    test.store(root / "rollback.compose.json", live)

    newer = json.loads(
        test.compose(root / "source/deploy/compose.production.yml", "config", "--format", "json")
    )
    definition = newer["services"][scope.service]
    current = live["services"][scope.service]
    # Keep operator-specific settings (notification backend, sync interval) from the live model.
    definition["environment"] = {
        **definition.get("environment", {}),
        **current.get("environment", {}),
    }
    if current.get("user"):
        definition["user"] = current["user"]
    definition["image"] = image_id
    candidate = json.loads(json.dumps(live))
    candidate["services"][scope.service] = definition
    for mount in definition.get("volumes", []):
        if mount["type"] == "volume" and mount["source"] not in candidate.get("volumes", {}):
            candidate.setdefault("volumes", {})[mount["source"]] = newer["volumes"][mount["source"]]
        if mount["type"] == "bind" and mount["source"].startswith("/var/log/factorlab/"):
            Path(mount["source"]).mkdir(parents=True, exist_ok=True, mode=0o750)
    test.store(root / "candidate.compose.json", candidate)
    test.compose(root / "candidate.compose.json", "config", "--quiet")
    # Import the component before anything live is touched.
    test.run(
        "docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
        image_id, "-c", f"import {scope.module}",
    )  # fmt: skip
    check_protected(root, scope)
    (root / "plan-ready").touch(mode=0o600)
    print("Plan validated; production containers still unchanged", flush=True)


def agent_ready() -> float:
    """Modification time of the secrets agent's readiness marker (0 when absent)."""
    output = test.run(
        "docker", "exec", container("cloudflare-secrets-agent"), "python", "-c",
        f"import os; p = {AGENT_READY!r}; print(os.stat(p).st_mtime if os.path.exists(p) else 0)",
    )  # fmt: skip
    return float(output.strip() or 0)


def wait(check, what: str, timeout: float | None = None) -> None:
    deadline = time.monotonic() + (HEALTH_TIMEOUT if timeout is None else timeout)
    while not check():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {what}")
        time.sleep(3)


def healthy(service: str) -> bool:
    [state] = test.inspect([container(service)])
    if not state["State"]["Running"] or state["RestartCount"]:
        raise RuntimeError(f"Unstable service: {service}")
    return state["State"].get("Health", {}).get("Status") == "healthy"


def monitor_online() -> bool:
    try:
        with urllib.request.urlopen(API + "/hub/api/v1/monitoring/services", timeout=30) as r:
            return r.status == 200 and json.load(r).get("monitor_offline") is False
    except OSError:
        return False


def verify(root: Path, scope: Scope, ready_before: float | None = None) -> None:
    candidate = json.loads((root / "candidate.compose.json").read_text())
    if scope.service == "bootstrap":
        print("Schema migrator pinned; no long-running container to check", flush=True)
    else:
        wait(lambda: healthy(scope.service), f"{scope.service} health")
        if ready_before is not None:
            wait(lambda: agent_ready() > ready_before, "a fresh secrets-agent sync")
        if scope.service == "monitor":
            wait(monitor_online, "the API to see the monitor online", timeout=150)
        [state] = test.inspect([container(scope.service)])
        if state["Image"] != candidate["services"][scope.service]["image"]:
            raise RuntimeError(f"Image mismatch: {scope.service}")
        test.store(root / "after-container.json", test.public_state([container(scope.service)]))
    check_protected(root, scope)
    print(f"{scope.service} verified; protected containers unchanged", flush=True)


def check_schema(model: Path, root: Path) -> None:
    """Run the new migrator's read-only checks against the live database."""
    output = test.compose(model, "run", "--rm", "--no-deps", "bootstrap")
    output += test.compose(model, "run", "--rm", "--no-deps", "bootstrap", "migrate", "status")
    (root / "schema-status.txt").write_text(output)
    (root / "schema-status.txt").chmod(0o600)
    print(output.strip(), flush=True)


def restore(root: Path, scope: Scope) -> None:
    rollback = root / "rollback.compose.json"
    test.install_model(rollback)
    if scope.service != "bootstrap":
        test.compose(
            test.DEPLOY / "compose.production.yml",
            "up", "-d", "--no-deps", "--force-recreate", scope.service,
        )  # fmt: skip
        wait(lambda: healthy(scope.service), f"restored {scope.service} health")
    (root / "restored").touch(mode=0o600)
    check_protected(root, scope)
    print(f"Previous {scope.service} image restored", flush=True)


def apply(root: Path, scope: Scope) -> None:
    if not (root / "plan-ready").exists() or (root / "applied").exists():
        raise RuntimeError("Need a validated, unapplied plan")
    if test.public_state(existing([container(scope.service)])) != json.loads(
        (root / "before-container.json").read_text()
    ):
        raise RuntimeError(f"{scope.service} changed since plan; refusing stale replacement")
    check_protected(root, scope)
    ready_before = agent_ready() if scope.unit == "secrets-agent" else None
    try:
        if scope.service == "bootstrap":
            check_schema(root / "candidate.compose.json", root)
        test.install_model(root / "candidate.compose.json")
        if scope.service != "bootstrap":
            test.compose(
                test.DEPLOY / "compose.production.yml",
                "up", "-d", "--no-deps", "--force-recreate", scope.service,
            )  # fmt: skip
        verify(root, scope, ready_before)
    except Exception:
        restore(root, scope)
        raise
    (root / "applied").touch(mode=0o600)


def run_trial(root: Path, test_id: str, scope_name: str, commit: str, image: str, model: Path):
    scope = SCOPES[scope_name]
    trial.prepare(root, model, RUNNERS)
    image_id = trial.install_image(image, scope.unit, commit, test_id, scope.user)
    (root / "commit").write_text(commit + "\n")
    test.store(root / "scope.json", {"scope": scope_name})
    plan(root, scope, image_id)
    print("FACTORLAB_RESULT_PLAN=passed", flush=True)
    try:
        apply(root, scope)
    except Exception:
        print("FACTORLAB_RESULT_ROLLBACK=restored", flush=True)
        raise
    print("FACTORLAB_RESULT_VERIFICATION=passed", flush=True)
    print("FACTORLAB_RESULT_ROLLBACK=not-needed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "verify", "restore"))
    parser.add_argument("test_id")
    parser.add_argument("--scope", choices=tuple(SCOPES))
    parser.add_argument("--commit")
    parser.add_argument("--image")
    parser.add_argument("--model", type=Path, help="candidate compose.production.yml")
    args = parser.parse_args()
    if os.geteuid() != 0 or not TRIAL_RE.fullmatch(args.test_id):
        raise RuntimeError("Run as root with a YYYYMMDD-<12 hex> trial ID")
    root = test.BASE / args.test_id
    with Path("/var/lock/factorlab-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "apply":
            if not args.commit or not trial.COMMIT_RE.fullmatch(args.commit):
                raise RuntimeError("A full commit SHA is required")
            if not args.test_id.endswith(f"-{args.commit[:12]}"):
                raise RuntimeError("Trial ID must name the commit")
            if not (args.scope and args.image and args.model and args.model.is_file()):
                raise RuntimeError("apply needs --scope, --image and --model")
            run_trial(root, args.test_id, args.scope, args.commit, args.image, args.model)
            return
        if not (root / "scope.json").is_file():
            raise RuntimeError("Unknown trial ID")
        scope = SCOPES[json.loads((root / "scope.json").read_text())["scope"]]
        if args.command == "verify":
            verify(root, scope)
            print("FACTORLAB_RESULT_VERIFICATION=passed", flush=True)
        else:
            restore(root, scope)
            print("FACTORLAB_RESULT_ROLLBACK=restored", flush=True)


if __name__ == "__main__":
    main()
