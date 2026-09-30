"""Explicitly authorized monitoring extension of the existing VPS branch trial.

Run as root after building tracked source images. Saves a pinned rollback model;
wave 11 is forward-only and is never removed by rollback. Resolved models stay
root-only because they can contain runtime secrets.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import ingestion_test as test

UNITS = (
    "monitor",
    "api",
    "web",
    "secrets-agent",
    "schema-migrator",
    "ingest-india",
    "ingest-us",
    "ingest-political",
    "ingest-broker",
)
CHANGED = ["cloudflare-secrets-agent", "monitor", "api-candidate", "web", *test.DAEMONS]
REGISTRY = Path("/etc/factorlab/monitor-services.yaml")


def image(unit: str, test_id: str) -> str:
    metadata = json.loads(
        test.run("docker", "image", "inspect", f"factorlab-test-{unit}:{test_id}")
    )[0]
    labels = metadata["Config"]["Labels"]
    if labels.get("io.factorlab.component") != unit:
        raise RuntimeError(f"Wrong image component: {unit}")
    if not labels.get("org.opencontainers.image.revision", "").startswith(test_id.split("-", 1)[1]):
        raise RuntimeError(f"Wrong source revision: {unit}")
    return metadata["Id"]


def plan(root: Path, test_id: str) -> None:
    if (root / "before.json").exists():
        raise RuntimeError("Trial already prepared")
    live = json.loads(
        test.compose(test.DEPLOY / "compose.production.yml", "config", "--format", "json")
    )
    ids = test.run(
        "docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=factorlab"
    ).split()
    containers = json.loads(test.run("docker", "inspect", *ids)) if ids else []
    for container in containers:
        service = container["Config"]["Labels"].get("com.docker.compose.service")
        if service in live["services"]:
            live["services"][service]["image"] = container["Image"]
    test.store(root / "before.json", live)
    test.store(
        root / "protected.json", test.public_state(["factorlab-api-1", "factorlab-clickhouse"])
    )
    newer = json.loads(
        test.compose(root / "source/deploy/compose.production.yml", "config", "--format", "json")
    )
    candidate = json.loads(json.dumps(live))
    mapping = {
        **test.SERVICES,
        "api-candidate": "api",
        "web": "web",
        "cloudflare-secrets-agent": "secrets-agent",
        "bootstrap": "schema-migrator",
    }
    for service, unit in mapping.items():
        candidate["services"][service]["image"] = image(unit, test_id)
    # Preserve all live provider settings, Gateway session configuration and ports.
    for service in ["api-candidate", *test.SERVICES]:
        candidate["services"][service].setdefault("environment", {})["FACTORLAB_MONITOR_ADDR"] = (
            "monitor:9125"
        )
    candidate["services"]["api-candidate"]["environment"]["FACTORLAB_SERVICE"] = "api"
    registry_mount = {
        "type": "bind",
        "source": str(REGISTRY),
        "target": "/app/monitor-services.yaml",
        "read_only": True,
    }
    candidate["services"]["api-candidate"].setdefault("volumes", []).append(registry_mount)
    candidate["volumes"]["monitor_runtime"] = newer["volumes"]["monitor_runtime"]
    agent_mounts = candidate["services"]["cloudflare-secrets-agent"]["volumes"]
    agent_mounts.append(
        {
            "type": "volume",
            "source": "monitor_runtime",
            "target": "/run/secrets/monitor",
            "volume": {},
        }
    )
    candidate["services"]["monitor"] = newer["services"]["monitor"]
    candidate["services"]["monitor"]["image"] = image("monitor", test_id)
    # Alerts remain local unless the owner explicitly configures a delivery backend.
    candidate["services"]["monitor"]["environment"]["FACTORLAB_NOTIFY_BACKEND"] = "jsonl"
    test.store(root / "candidate.json", candidate)
    test.compose(root / "candidate.json", "config", "--quiet")
    if REGISTRY.exists():
        shutil.copy2(REGISTRY, root / "registry-before.yaml")
    print("Pinned rollback and candidate validated; live services unchanged", flush=True)


def wait_health(service: str) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        container = test.inspect([f"factorlab-{service}-1"])[0]
        if not container["State"]["Running"] or container["RestartCount"]:
            raise RuntimeError(f"Unstable service: {service}")
        if container["State"].get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(3)
    raise RuntimeError(f"Health timeout: {service}")


def migration(root: Path) -> None:
    # Use the reviewed runner and journal, with an explicit wave-11-only selection.
    code = """
from factorlab.schema import migrate as m
client = m.create_client()
try:
    migrations = m.discover_migrations()
    m.ensure_no_checksum_drift(migrations, m.load_journal(client))
    chosen = [item for item in migrations if item.migration_id == 'wave_11_schema_service_monitoring']
    assert len(chosen) == 1
    args = m.build_parser().parse_args(['apply', '--phase', 'schema', '--through-wave', '11', '--yes'])
    m.command_apply(client, args, chosen)
    assert m.load_journal(client)[chosen[0].migration_id].status == 'succeeded'
    assert client.query("SELECT count() FROM system.tables WHERE database='meta' AND name IN ('service_events','service_status')").result_rows[0][0] == 2
finally:
    client.close()
"""
    output = test.compose(
        root / "candidate.json",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "python",
        "bootstrap",
        "-c",
        code,
    )
    (root / "migration-result.txt").write_text(output)
    print(output.strip(), flush=True)


def restore(root: Path) -> None:
    for backup, target in (
        (root / "production.env.before", test.DEPLOY / "production.env"),
        (root / "logrotate-before", Path("/etc/logrotate.d/factorlab")),
    ):
        if backup.exists():
            shutil.copy2(backup, target)
    shutil.copy2(root / "before.json", test.DEPLOY / "compose.production.yml")
    if (root / "registry-before.yaml").exists():
        shutil.copy2(root / "registry-before.yaml", REGISTRY)
    test.compose(root / "candidate.json", "stop", "monitor")
    test.compose(
        root / "before.json",
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        *[s for s in CHANGED if s != "monitor"],
    )
    print("Previous images restored; additive wave 11 retained", flush=True)


def apply(root: Path) -> None:
    if (root / "applied").exists():
        raise RuntimeError("Trial already applied")
    migration(root)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "source/deploy/monitor-services.yaml", REGISTRY)
    REGISTRY.chmod(0o644)
    log_dir = Path("/var/log/factorlab/monitor")
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chown(log_dir, 10001, Path("/var/log/factorlab/api").stat().st_gid)
    log_dir.chmod(0o2750)
    try:
        test.compose(root / "before.json", "stop", *test.DAEMONS)
        shutil.copy2(root / "candidate.json", test.DEPLOY / "compose.production.yml")
        test.compose(
            root / "candidate.json",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "cloudflare-secrets-agent",
        )
        wait_health("cloudflare-secrets-agent")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "factorlab-cloudflare-secrets-agent-1",
                    "test",
                    "-s",
                    "/run/secrets/monitor/CLICKHOUSE_PASSWORD",
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                break
            time.sleep(3)
        else:
            raise RuntimeError("Monitor secret was not rendered")
        test.compose(root / "candidate.json", "up", "-d", "--no-deps", "monitor")
        wait_health("monitor")
        test.compose(
            root / "candidate.json",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "api-candidate",
            "web",
            *test.DAEMONS,
        )
        verify(root)
        (root / "applied").write_text("ok\n")
    except Exception:
        restore(root)
        raise


def verify(root: Path) -> None:
    candidate = json.loads((root / "candidate.json").read_text())
    for service in CHANGED:
        container = test.inspect([f"factorlab-{service}-1"])[0]
        if (
            not container["State"]["Running"]
            or container["RestartCount"]
            or container["Image"] != candidate["services"][service]["image"]
        ):
            raise RuntimeError(f"Wrong or unstable image: {service}")
    for service in ("cloudflare-secrets-agent", "monitor", "api-candidate", "web"):
        wait_health(service)
    if test.public_state(["factorlab-api-1", "factorlab-clickhouse"]) != json.loads(
        (root / "protected.json").read_text()
    ):
        raise RuntimeError("Protected primary API or ClickHouse changed")
    for url in (
        "http://127.0.0.1:8080/monitoring",
        "http://127.0.0.1:18000/health",
        "http://127.0.0.1:18000/hub/api/v1/overview",
    ):
        with urllib.request.urlopen(url, timeout=30) as response:
            assert response.status == 200
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        with urllib.request.urlopen(
            "http://127.0.0.1:18000/hub/api/v1/monitoring/services", timeout=30
        ) as response:
            result = json.load(response)
        (root / "services-result.json").write_text(json.dumps(result, indent=2))
        services = {row["service_id"]: row for row in result["services"]}
        expected = {"monitor", "api", "ingest-india", "ingest-us", "universe-us", "ibkr-snapshot"}
        current = expected - {"ibkr-snapshot"}
        if (
            not result["monitor_offline"]
            and services.get("ibkr-snapshot", {}).get("last_seen_at") is not None
            and all(services.get(name, {}).get("status") == "up" for name in current)
        ):
            print("Monitor, API/UI, current images and heartbeat receipt verified", flush=True)
            print(
                json.dumps({name: services[name]["status"] for name in sorted(expected)}),
                flush=True,
            )
            return
        time.sleep(5)
    raise RuntimeError("Sender heartbeats did not become current; see private services-result.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply", "verify", "restore"))
    parser.add_argument("test_id")
    args = parser.parse_args()
    root = test.BASE / args.test_id
    if os.geteuid() != 0 or not root.is_dir() or root.parent != test.BASE or "/" in args.test_id:
        raise RuntimeError("Run as root against an existing test directory")
    with Path("/var/lock/factorlab-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "plan":
            plan(root, args.test_id)
        else:
            {"apply": apply, "verify": verify, "restore": restore}[args.command](root)


if __name__ == "__main__":
    main()
