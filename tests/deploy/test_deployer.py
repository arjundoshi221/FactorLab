"""deploy/host/factorlab_deploy.py against a fake Docker: success, recovery classes, refusals."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
import tarfile
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("factorlab_deploy", REPO / "deploy/host/factorlab_deploy.py")
fd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fd
spec.loader.exec_module(fd)

OLD = "ghcr.io/arjundoshi221/factorlab-api@sha256:" + "a" * 64
NEW = "ghcr.io/arjundoshi221/factorlab-api@sha256:" + "b" * 64
BAD = "ghcr.io/arjundoshi221/factorlab-api@sha256:" + "c" * 64
MONOLITH = "ghcr.io/arjundoshi221/factorlab@sha256:" + "d" * 64

FRAGMENT = {
    "services": {"api": {
        "image": "${FACTORLAB_API_IMAGE:-${FACTORLAB_IMAGE:-factorlab:latest}}",
        "logging": {"driver": "json-file"},
        "ports": ["127.0.0.1:8000:8000"],
        "volumes": ["political_runtime:/run/secrets/app:ro",
                    "/var/log/factorlab/api:/var/log/factorlab/api"],
    }},
}


def manifest(version: str, *, rollback: str = "auto", contract: int | None = None,
             name: str = "api", services=None) -> dict:
    platform = {"image_var": f"FACTORLAB_{name.upper().replace('-', '_')}_IMAGE",
                "rollback": rollback, "user": "10001", "logs": f"/var/log/factorlab/{name}"}
    if contract is not None:
        platform["data_contract"] = contract
    return {"name": name, "version": version, "platform": platform,
            "services": services or [{"name": "api", "mode": "daemon"}]}


class FakeDocker:
    """Just enough of `docker` and `docker compose` for the deployer."""

    def __init__(self) -> None:
        self.images: dict[str, dict] = {}
        self.containers: dict[str, dict] = {}
        self.calls: list[list[str]] = []
        self.recreated: list[str] = []
        self.prepare_host_rc = 0

    def add_image(self, ref: str, version: str, *, healthy: bool = True, component: str = "api"):
        self.images[ref] = {"id": "sha256:" + ref[-64:], "healthy": healthy, "labels": {
            "io.factorlab.component": component, "org.opencontainers.image.version": version}}

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        if cmd[0] == "bash":
            return self._result(self.prepare_host_rc)
        args = list(cmd[1:])
        if args[0] == "compose":
            return self._compose(args[1:])
        if args[0] == "pull":
            return self._result(0 if args[-1] in self.images else 1)
        if args[:2] == ["image", "inspect"]:
            image = self.images[args[-1]]
            if "{{.Id}}" in args:
                return self._result(0, image["id"])
            return self._result(0, json.dumps(image["labels"]))
        if args[0] == "inspect":
            container = next(c for c in self.containers.values() if c["id"] == args[1])
            return self._result(0, json.dumps([{
                "Image": container["image_id"], "RestartCount": 0,
                "Config": {"Labels": {"com.docker.compose.config-hash": container.get("hash", "")}},
                "State": {"Status": container["status"], "Health": None}}]))
        raise AssertionError(f"unexpected docker call {cmd}")

    @staticmethod
    def _result(code: int, out: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], code, out, "")

    def _compose(self, args):
        env, files, rest, i = {}, [], [], 0
        while i < len(args):
            if args[i] in {"--project-name", "--project-directory", "--profile"}:
                i += 2
            elif args[i] == "--env-file":
                env.update(fd.read_env(Path(args[i + 1])))
                i += 2
            elif args[i] == "-f":
                files.append(Path(args[i + 1]))
                i += 2
            else:
                rest.append(args[i])
                i += 1
        model = self._model(files, env)
        verb = rest[0]
        if verb == "config":
            if "--hash" in rest:
                return self._result(0, "".join(f"{name} h-{name}\n" for name in model["services"]))
            if "--services" in rest:
                return self._result(0, "".join(f"{name}\n" for name in model["services"]))
            if "--format" in rest:
                return self._result(0, json.dumps(model))
            return self._result(0)
        name = rest[-1]
        if verb == "ps":
            container = self.containers.get(name)
            return self._result(0, container["id"] if container else "")
        image = self.images[model["services"][name]["image"]]
        if verb == "up":
            self.recreated.append(name)
            self.containers[name] = {"id": f"c-{name}-{len(self.recreated)}", "image_id": image["id"],
                                     "status": "running" if image["healthy"] else "exited"}
            return self._result(0)
        if verb == "run":
            return self._result(0 if image["healthy"] else 1)
        if verb == "stop":
            if name in self.containers:
                self.containers[name]["status"] = "exited"
            return self._result(0)
        raise AssertionError(f"unexpected compose verb {rest}")

    @staticmethod
    def _resolve(value: str, env: dict[str, str]) -> str:
        pattern = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^${}]*))?\}")
        while "${" in value:
            value = pattern.sub(lambda m: env.get(m.group(1)) or (m.group(2) or ""), value)
        return value

    def _model(self, files, env):
        services = {}
        for path in files:
            for name, service in (yaml.safe_load(path.read_text()) or {}).get("services", {}).items():
                ports = []
                for port in service.get("ports", []):
                    parts = str(port).split(":")
                    ports.append({"host_ip": parts[0] if len(parts) == 3 else "",
                                  "published": parts[-2], "target": parts[-1]})
                volumes = []
                for volume in service.get("volumes", []):
                    source, target, *mode = str(volume).split(":")
                    volumes.append({"type": "bind" if source.startswith("/") else "volume",
                                    "source": source, "target": target,
                                    "read_only": mode == ["ro"]})
                services[name] = {**service, "image": self._resolve(service["image"], env),
                                  "ports": ports, "volumes": volumes}
        return {"services": services}


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "opt"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "compose.base.yml").write_text("name: factorlab\nservices: {}\n")
    (root / "deploy" / "production.env").write_text("CLOUDFLARE_SECRETS_URL=https://x\n")
    live = root / "components" / "api"
    live.mkdir(parents=True)
    (live / "compose.yaml").write_text(yaml.safe_dump(FRAGMENT))
    (live / "component.json").write_text(json.dumps(manifest("1.0.0")))
    (root / "state").mkdir()
    (root / "state" / "images.env").write_text(f"FACTORLAB_API_IMAGE={OLD}\n")
    (root / "releases" / "api" / "1.0.0").mkdir(parents=True)
    (root / "releases" / "api" / "current").write_text("1.0.0\n")
    (root / "releases" / "platform").mkdir()
    (root / "releases" / "platform" / "current").write_text("0.9.0\n")
    docker = FakeDocker()
    docker.add_image(OLD, "1.0.0")
    docker.add_image(NEW, "1.1.0")
    docker.add_image(BAD, "1.2.0", healthy=False)
    docker.containers["api"] = {"id": "c-api-0", "image_id": docker.images[OLD]["id"],
                                "status": "running"}
    host = fd.Host(root=root, lock_path=tmp_path / "lock", run=docker, sleep=lambda s: None,
                   chown=lambda *a: None, lock=lambda path: nullcontext(), stabilize_seconds=0,
                   health_timeout=5)
    clock = iter(range(10_000))
    host.clock = lambda: next(clock)
    logs: list[str] = []
    return docker, fd.Deployer(host, log=logs.append), root


def _tgz(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            info = tarfile.TarInfo(name)
            payload = text.encode()
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def bundle(tmp_path, component_manifest: dict, fragment: dict | None = None, extra=None) -> Path:
    files = {"component.json": json.dumps(component_manifest),
             "compose.yaml": yaml.safe_dump(fragment or FRAGMENT), **dict(extra or [])}
    return _tgz(tmp_path / f"bundle-{component_manifest['version']}.tgz", files)


def pin(root: Path) -> str:
    return fd.read_env(root / "state" / "images.env")["FACTORLAB_API_IMAGE"]


def test_successful_deploy_swaps_pin_and_recreates_only_the_component(world, tmp_path):
    docker, deployer, root = world
    outcome = deployer.deploy("api", "1.1.0", NEW, bundle(tmp_path, manifest("1.1.0")))
    assert (outcome.verification, outcome.previous) == ("succeeded", "1.0.0")
    assert pin(root) == NEW and docker.recreated == ["api"]
    assert (root / "releases/api/current").read_text().strip() == "1.1.0"
    assert (root / "releases/api/1.1.0/result").read_text().strip() == "succeeded"
    assert json.loads((root / "releases/api/1.1.0/previous.json").read_text())["image"] == OLD
    assert "succeeded" in (root / "releases/api/history.log").read_text()


def test_unhealthy_auto_release_rolls_back_to_the_previous_image(world, tmp_path):
    docker, deployer, root = world
    outcome = deployer.deploy("api", "1.2.0", BAD, bundle(tmp_path, manifest("1.2.0")))
    assert (outcome.verification, outcome.rollback) == ("failed", "rolled-back")
    assert pin(root) == OLD
    assert docker.containers["api"]["image_id"] == docker.images[OLD]["id"]
    assert (root / "releases/api/current").read_text().strip() == "1.0.0"


def test_writer_with_a_new_data_contract_stops_for_fix_forward(world, tmp_path):
    docker, deployer, root = world
    (root / "components/api/component.json").write_text(
        json.dumps(manifest("1.0.0", rollback="writer", contract=1)))
    outcome = deployer.deploy("api", "1.2.0", BAD,
                              bundle(tmp_path, manifest("1.2.0", rollback="writer", contract=2)))
    assert outcome.rollback == "fix-forward-required"
    assert docker.containers["api"]["status"] == "exited"


def test_writer_with_the_same_contract_rolls_back(world, tmp_path):
    _, deployer, root = world
    (root / "components/api/component.json").write_text(
        json.dumps(manifest("1.0.0", rollback="writer", contract=1)))
    outcome = deployer.deploy("api", "1.2.0", BAD,
                              bundle(tmp_path, manifest("1.2.0", rollback="writer", contract=1)))
    assert outcome.rollback == "rolled-back" and pin(root) == OLD


def test_forward_only_component_is_not_rolled_back(world, tmp_path):
    _, deployer, _ = world
    outcome = deployer.deploy("api", "1.2.0", BAD,
                              bundle(tmp_path, manifest("1.2.0", rollback="forward-only")))
    assert outcome.rollback == "failed-forward-only"


@pytest.mark.parametrize("image", ["ghcr.io/arjundoshi221/factorlab@sha256:" + "b" * 64,
                                   "ghcr.io/someone-else/factorlab-api@sha256:" + "b" * 64,
                                   "ghcr.io/arjundoshi221/factorlab-api:1.1.0"])
def test_refuses_images_that_are_not_this_components_digest(world, tmp_path, image):
    _, deployer, root = world
    with pytest.raises(fd.DeployError, match="image must be"):
        deployer.deploy("api", "1.1.0", image, bundle(tmp_path, manifest("1.1.0")))
    assert pin(root) == OLD


def test_refuses_a_label_mismatch_before_touching_production(world, tmp_path):
    docker, deployer, root = world
    docker.add_image(NEW, "9.9.9")
    with pytest.raises(fd.DeployError, match="version label"):
        deployer.deploy("api", "1.1.0", NEW, bundle(tmp_path, manifest("1.1.0")))
    assert pin(root) == OLD and docker.recreated == []


@pytest.mark.parametrize(("change", "message"), [
    ({"ports": ["8000:8000"]}, "beyond loopback"),
    ({"volumes": ["/etc/passwd:/x"]}, "outside the allowed host paths"),
    ({"privileged": True}, "may not set"),
])
def test_refuses_fragments_that_break_platform_policy(world, tmp_path, change, message):
    docker, deployer, root = world
    fragment = {"services": {"api": {**FRAGMENT["services"]["api"], **change}}}
    with pytest.raises(fd.DeployError, match=message):
        deployer.deploy("api", "1.1.0", NEW, bundle(tmp_path, manifest("1.1.0"), fragment))
    assert pin(root) == OLD and docker.recreated == []


def test_refuses_component_deploys_before_the_platform_bootstrap(world, tmp_path):
    docker, deployer, root = world
    (root / "releases/platform/current").unlink()
    with pytest.raises(fd.DeployError, match=r"deploy-platform\.sh first"):
        deployer.deploy("api", "1.1.0", NEW, bundle(tmp_path, manifest("1.1.0")))
    assert pin(root) == OLD and docker.recreated == []


def test_refuses_bundles_that_escape_the_staging_directory(world, tmp_path):
    _, deployer, _ = world
    evil = bundle(tmp_path, manifest("1.1.0"), extra=[("../../escape.txt", "x")])
    with pytest.raises(fd.DeployError, match="refusing bundle entry"):
        deployer.deploy("api", "1.1.0", NEW, evil)


def test_rollback_redeploys_the_previous_version(world, tmp_path):
    _, deployer, root = world
    # 1.0.0 needs a recorded bundle to return to: deploy it first, then 1.1.0.
    (root / "releases/api/current").unlink()
    (root / "state/images.env").write_text("")
    first = deployer.deploy("api", "1.0.0", OLD, bundle(tmp_path, manifest("1.0.0")))
    second = deployer.deploy("api", "1.1.0", NEW, bundle(tmp_path, manifest("1.1.0")))
    assert (first.verification, second.verification) == ("succeeded", "succeeded")
    outcome = deployer.rollback("api")
    assert (outcome.version, outcome.verification) == ("1.0.0", "succeeded")
    assert pin(root) == OLD


def test_seed_installs_missing_fragments_and_never_overwrites(world, tmp_path):
    _, deployer, root = world
    platform = tmp_path / "platform"
    for name in ("api", "ingest-us"):
        folder = platform / "components" / name
        folder.mkdir(parents=True)
        (folder / "compose.yaml").write_text(yaml.safe_dump({"services": {}}))
        (folder / "component.json").write_text(json.dumps(manifest("0.0.0", name=name)))
    before = (root / "components/api/compose.yaml").read_text()
    deployer.seed(platform, MONOLITH)
    assert (root / "components/api/compose.yaml").read_text() == before
    assert (root / "components/ingest-us/compose.yaml").exists()
    pins = fd.read_env(root / "state/images.env")
    assert pins["FACTORLAB_API_IMAGE"] == OLD and pins["FACTORLAB_INGEST_US_IMAGE"] == MONOLITH


def platform_bundle(tmp_path, version: str) -> Path:
    return _tgz(tmp_path / f"platform-{version}.tgz", {
        "deploy/compose.base.yml": "name: factorlab\nservices: {}\n",
        "deploy/host/factorlab_deploy.py": "# host tool\n",
        "deploy/scripts/prepare-host.sh": "#!/bin/sh\n",
        "components/ingest-us/compose.yaml": yaml.safe_dump({"services": {}}),
        "components/ingest-us/component.json": json.dumps(manifest("0.0.0", name="ingest-us")),
        "components/api/compose.yaml": yaml.safe_dump({"services": {}}),
        "components/api/component.json": json.dumps(manifest("0.0.0")),
    })


def test_platform_release_keeps_settings_seeds_components_and_recreates_nothing(world, tmp_path):
    docker, deployer, root = world
    (root / "current-image").write_text(MONOLITH + "\n")
    api_fragment = (root / "components/api/compose.yaml").read_text()
    outcome = deployer.platform("1.0.0", platform_bundle(tmp_path, "1.0.0"))
    assert outcome.verification == "succeeded"
    assert (root / "deploy/production.env").read_text().startswith("CLOUDFLARE_SECRETS_URL")
    assert (root / "deploy/host/factorlab_deploy.py").exists()
    assert (root / "components/api/compose.yaml").read_text() == api_fragment
    assert fd.read_env(root / "state/images.env")["FACTORLAB_INGEST_US_IMAGE"] == MONOLITH
    assert (root / "releases/platform/current").read_text().strip() == "1.0.0"
    assert docker.recreated == []
    # The running api container carries no matching config hash, so it is reported.
    assert (root / "releases/platform/1.0.0/drift").read_text().split() == ["api"]


def test_the_repositorys_platform_bundle_installs_and_seeds_the_monolith(world, tmp_path):
    docker, deployer, root = world
    tool_spec = importlib.util.spec_from_file_location("components_tool", REPO / "tools/components.py")
    tool = importlib.util.module_from_spec(tool_spec)
    sys.modules[tool_spec.name] = tool
    tool_spec.loader.exec_module(tool)
    out = tmp_path / "platform.tgz"
    version = tool.build_bundle("platform", out, tool.load_all())
    (root / "current-image").write_text(MONOLITH + "\n")
    outcome = deployer.platform(version, out)
    assert outcome.verification == "succeeded" and docker.recreated == []
    pins = fd.read_env(root / "state/images.env")
    assert pins["FACTORLAB_API_IMAGE"] == OLD  # already component-managed: untouched
    assert pins["FACTORLAB_INGEST_INDIA_IMAGE"] == MONOLITH
    assert {p.parent.name for p in (root / "components").glob("*/compose.yaml")} == {
        c.name for c in tool.load_all()}


def test_platform_release_restores_the_previous_bundle_when_prepare_host_fails(world, tmp_path):
    docker, deployer, root = world
    (root / "deploy/marker").write_text("previous platform")
    docker.prepare_host_rc = 1
    outcome = deployer.platform("1.0.1", platform_bundle(tmp_path, "1.0.1"))
    assert (outcome.verification, outcome.rollback) == ("failed", "rolled-back")
    assert (root / "deploy/marker").read_text() == "previous platform"
    assert (root / "releases/platform/current").read_text().strip() == "0.9.0"
