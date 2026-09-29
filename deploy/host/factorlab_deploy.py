#!/usr/bin/env python3
"""Per-component deployer for the FactorLab VPS (stdlib only; runs as root via sudo).

    factorlab_deploy.py deploy <component> <version> <image@sha256:...> <bundle.tgz>
    factorlab_deploy.py rollback <component> [--to <version>]
    factorlab_deploy.py platform <version> <platform-bundle.tgz>
    factorlab_deploy.py seed <platform-bundle-dir> <image@sha256:...>   (bridge from the monolith)
    factorlab_deploy.py status
    factorlab_deploy.py compose -- <docker compose arguments...>

Host layout (FACTORLAB_HOST_ROOT defaults to /opt/factorlab):

    deploy/                        platform bundle: compose.base.yml, scripts, host tools,
                                   production.env (operator settings)
    components/<name>/compose.yaml the component's compose fragment (component-owned)
    components/<name>/component.json
    state/images.env               FACTORLAB_<NAME>_IMAGE=...@sha256 pins (deployer-owned)
    releases/<name>/<version>/     bundle, previous pin, running images, result, deploy.log

A deploy validates everything before touching production. It then records the
current state, swaps the component's fragment and image pin atomically, recreates
only that component's services, and verifies them. On failure it acts by the
manifest's rollback class:

    auto          restore the previous fragment and pin, recreate, verify
    writer        the same when the data_contract is unchanged; otherwise stop the
                  component's services and report fix-forward-required
    forward-only  report failed; nothing is rolled back (schema operations)

The last lines printed are FACTORLAB_RESULT_* key=value pairs for the release workflow.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = "ghcr.io/arjundoshi221"
NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-rc\.\d+)?$")
DIGEST = r"@sha256:[0-9a-f]{64}"
FORBIDDEN_KEYS = ("privileged", "network_mode", "pid", "ipc", "cap_add", "container_name")
# Host paths a component may bind-mount; everything else in a fragment is refused.
ALLOWED_HOST_PREFIXES = ("/var/log/factorlab/", "/var/lib/factorlab/app-data",
                         "/var/lib/factorlab/docker-images", "/etc/factorlab/")
PROFILE_FLAGS = {"FACTORLAB_IBKR_ENABLED": "ibkr", "FACTORLAB_IBKR_VPS_GATEWAYS": "ibkr-gateway",
                 "FACTORLAB_WEB_ENABLED": "web"}


class DeployError(RuntimeError):
    """A deployment refused or failed; the message says why."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


@contextlib.contextmanager
def release_lock(path: Path, timeout: float = 1200) -> Iterator[None]:
    """The host-wide release lock, shared with the legacy deploy-release.sh."""
    import fcntl  # POSIX only; the deployer runs on the Linux VPS

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise DeployError("another FactorLab release holds the lock") from None
                time.sleep(2)
        yield


@dataclass
class Host:
    root: Path = field(default_factory=lambda: Path(os.getenv("FACTORLAB_HOST_ROOT",
                                                              "/opt/factorlab")))
    lock_path: Path = field(default_factory=lambda: Path(os.getenv(
        "FACTORLAB_RELEASE_LOCK", "/var/lock/factorlab-release.lock")))
    run: Runner = _run
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    # A lambda, not _chown_tree itself: that function is defined below this class.
    chown: Callable[[Path, int, int], None] = lambda path, uid, gid: _chown_tree(path, uid, gid)  # noqa: PLW0108
    lock: Callable[[Path], contextlib.AbstractContextManager[None]] = release_lock
    stabilize_seconds: float = float(os.getenv("FACTORLAB_STABILIZATION_SECONDS", "20"))
    health_timeout: float = 180.0

    @property
    def platform(self) -> Path:
        return self.root / "deploy"

    @property
    def components(self) -> Path:
        return self.root / "components"

    @property
    def images_env(self) -> Path:
        return self.root / "state" / "images.env"

    def releases(self, component: str) -> Path:
        return self.root / "releases" / component

    # ── docker / compose ────────────────────────────────────────────────────

    def docker(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = self.run(["docker", *args])
        if check and result.returncode:
            raise DeployError(f"docker {' '.join(args[:3])} failed: "
                              f"{(result.stderr or result.stdout).strip()[:500]}")
        return result

    def profiles(self) -> list[str]:
        settings = read_env(self.platform / "production.env")
        flags = ["--profile", "jobs"]
        for key, profile in PROFILE_FLAGS.items():
            if settings.get(key, "").lower() in {"1", "true", "yes"}:
                flags += ["--profile", profile]
        return flags

    def compose_files(self, override: dict[str, Path] | None = None) -> list[str]:
        files = ["-f", str(self.platform / "compose.base.yml")]
        fragments = {p.parent.name: p for p in self.components.glob("*/compose.yaml")}
        fragments.update(override or {})
        for name in sorted(fragments):
            files += ["-f", str(fragments[name])]
        return files

    def compose_args(self, *args: str, override: dict[str, Path] | None = None,
                     images: Path | None = None) -> list[str]:
        """``docker compose`` arguments for the live model (base, fragments, pins, profiles)."""
        env_files = ["--env-file", str(self.platform / "production.env")]
        pins = images or self.images_env
        if pins.exists():
            env_files += ["--env-file", str(pins)]
        return ["compose", "--project-name", "factorlab", "--project-directory",
                str(self.platform), *env_files, *self.compose_files(override),
                *self.profiles(), *args]

    def compose(self, *args: str, override: dict[str, Path] | None = None,
                images: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return self.docker(*self.compose_args(*args, override=override, images=images),
                           check=check)

    def container(self, service: str) -> str | None:
        out = self.compose("ps", "-q", service).stdout.strip()
        return out.splitlines()[0] if out else None

    def inspect(self, container: str) -> dict:
        return json.loads(self.docker("inspect", container).stdout)[0]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def set_pin(path: Path, variable: str, image: str | None) -> None:
    lines = [line for line in (path.read_text(encoding="utf-8").splitlines()
                               if path.exists() else [])
             if not line.startswith(f"{variable}=")]
    if image:
        lines.append(f"{variable}={image}")
    write_atomic(path, "".join(f"{line}\n" for line in sorted(lines)))


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    for current, _dirs, files in os.walk(path):
        os.chown(current, uid, gid)
        for name in files:
            with contextlib.suppress(FileNotFoundError):
                os.chown(os.path.join(current, name), uid, gid, follow_symlinks=False)


def extract_bundle(bundle: Path, into: Path) -> None:
    """Extract a release bundle; only plain files and directories, no escapes."""
    with tarfile.open(bundle, "r:gz") as archive:
        for member in archive.getmembers():
            target = (into / member.name).resolve()
            if not (member.isfile() or member.isdir()) or not target.is_relative_to(into.resolve()):
                raise DeployError(f"refusing bundle entry {member.name!r}")
        try:
            archive.extractall(into, filter="data")
        except TypeError:  # Python < 3.10.12 has no extraction filters; members were checked
            archive.extractall(into)


# ── validation ──────────────────────────────────────────────────────────────


@dataclass
class Release:
    component: str
    version: str
    image: str
    manifest: dict
    fragment: Path

    @property
    def image_var(self) -> str:
        return self.manifest["platform"]["image_var"]

    @property
    def services(self) -> list[dict]:
        return self.manifest["services"]


def validate_arguments(component: str, version: str, image: str) -> None:
    if not NAME.match(component):
        raise DeployError(f"invalid component name {component!r}")
    if not VERSION.match(version):
        raise DeployError(f"invalid version {version!r} (SemVer X.Y.Z)")
    if not re.fullmatch(re.escape(f"{REGISTRY}/factorlab-{component}") + DIGEST, image):
        raise DeployError(f"image must be {REGISTRY}/factorlab-{component}@sha256:<digest>")


def load_release(component: str, version: str, image: str, staged: Path) -> Release:
    manifest_path, fragment = staged / "component.json", staged / "compose.yaml"
    if not manifest_path.is_file() or not fragment.is_file():
        raise DeployError("bundle must contain component.json and compose.yaml")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != component or manifest.get("version") != version:
        raise DeployError(f"bundle is {manifest.get('name')} {manifest.get('version')}, "
                          f"not {component} {version}")
    return Release(component, version, image, manifest, fragment)


def lint_model(model: dict, release: Release) -> None:
    """Platform policy for the component's services in the merged Compose model."""
    services = model.get("services", {})
    for spec in release.services:
        name = spec["name"]
        service = services.get(name)
        if service is None:
            if spec.get("profile") or spec.get("mode") == "scheduled":
                continue  # profile-gated or job service: present only when enabled
            raise DeployError(f"fragment does not define service {name}")
        if service.get("image") != release.image:
            raise DeployError(f"{name} would run {service.get('image')}, not {release.image}")
        if bad := [key for key in FORBIDDEN_KEYS if key in service]:
            raise DeployError(f"{name} may not set {bad}")
        for port in service.get("ports", []):
            if port.get("host_ip") != "127.0.0.1":
                raise DeployError(f"{name} publishes {port} beyond loopback")
        for volume in service.get("volumes", []):
            source = str(volume.get("source", ""))
            if volume.get("type") == "bind" and not source.startswith(ALLOWED_HOST_PREFIXES):
                raise DeployError(f"{name} bind-mounts {source}, outside the allowed host paths")
        if "logging" not in service:
            raise DeployError(f"{name} has no logging limits")


# ── the deployer ────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    component: str
    version: str
    previous: str = "none"
    verification: str = "failed"
    rollback: str = "not-required"
    error: str = ""

    def lines(self) -> list[str]:
        return [f"FACTORLAB_RESULT_COMPONENT={self.component}",
                f"FACTORLAB_RESULT_VERSION={self.version}",
                f"FACTORLAB_RESULT_PREVIOUS_VERSION={self.previous}",
                f"FACTORLAB_RESULT_VERIFICATION={self.verification}",
                f"FACTORLAB_RESULT_ROLLBACK={self.rollback}"]


class Deployer:
    def __init__(self, host: Host, log: Callable[[str], None] = print) -> None:
        self.host = host
        self.log = log

    # state

    def current_version(self, component: str) -> str | None:
        marker = self.host.releases(component) / "current"
        return marker.read_text(encoding="utf-8").strip() if marker.exists() else None

    def installed_manifest(self, component: str) -> dict | None:
        path = self.host.components / component / "component.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    # steps

    def preflight(self, release: Release) -> None:
        with tempfile.TemporaryDirectory() as work:
            pins = Path(work) / "images.env"
            if self.host.images_env.exists():
                shutil.copy(self.host.images_env, pins)
            set_pin(pins, release.image_var, release.image)
            model = json.loads(self.host.compose(
                "config", "--format", "json", override={release.component: release.fragment},
                images=pins).stdout)
        lint_model(model, release)
        self.host.docker("pull", "--quiet", release.image)
        labels = self.host.docker("image", "inspect", "--format", "{{json .Config.Labels}}",
                                  release.image).stdout
        labels = json.loads(labels or "{}") or {}
        if labels.get("io.factorlab.component") != release.component:
            raise DeployError("image label io.factorlab.component does not match")
        if labels.get("org.opencontainers.image.version") != release.version:
            raise DeployError(f"image version label {labels.get('org.opencontainers.image.version')}"
                              f" != {release.version}")

    def enabled(self, services: list[dict]) -> list[dict]:
        active = set(self.host.compose("config", "--services").stdout.split())
        return [s for s in services if s["name"] in active]

    def fix_ownership(self, release: Release) -> None:
        user = str(release.manifest["platform"].get("user", "0"))
        if user == "0":
            return
        uid = int(user.split(":")[0])
        model = json.loads(self.host.compose("config", "--format", "json").stdout)
        for spec in release.services:
            for volume in model.get("services", {}).get(spec["name"], {}).get("volumes", []):
                source = Path(str(volume.get("source", "")))
                if (volume.get("type") == "bind" and not volume.get("read_only")
                        and str(source).startswith(("/var/log/factorlab/", "/var/lib/factorlab/"))
                        and source.is_dir()):
                    is_log_dir = str(source).startswith("/var/log/factorlab/")
                    self.host.chown(source, uid, 10002 if is_log_dir else uid)
                    if is_log_dir:
                        os.chmod(source, 0o2750)  # noqa: S103 - chown may clear setgid; others get nothing

    def roll(self, release: Release) -> None:
        for spec in self.enabled(release.services):
            name, mode = spec["name"], spec.get("mode", "daemon")
            if mode == "daemon":
                self.log(f"recreating {name}")
                self.host.compose("up", "-d", "--no-deps", "--force-recreate", name)
            elif mode == "run-on-deploy":
                self.log(f"running {name}")
                self.host.compose("run", "--rm", "--no-deps", name)
            # scheduled services start from their host timer or cron entry

    def verify(self, release: Release, image: str) -> None:
        expected = self.host.docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
        daemons = [s for s in self.enabled(release.services) if s.get("mode", "daemon") == "daemon"]
        deadline = self.host.clock() + self.host.health_timeout
        pending = {s["name"] for s in daemons}
        restarts: dict[str, int] = {}
        while pending:
            for name in sorted(pending):
                container = self.host.container(name)
                if not container:
                    raise DeployError(f"{name} has no container")
                state = self.host.inspect(container)
                if state["Image"] != expected:
                    raise DeployError(f"{name} runs {state['Image'][:19]}, not {expected[:19]}")
                status = state["State"]["Status"]
                health = (state["State"].get("Health") or {}).get("Status")
                if status in {"exited", "dead"} or health == "unhealthy":
                    raise DeployError(f"{name} is {health or status}")
                if status == "running" and health in {None, "healthy"}:
                    pending.discard(name)
                    restarts[name] = state.get("RestartCount", 0)
            if pending:
                if self.host.clock() > deadline:
                    raise DeployError(f"not healthy in time: {sorted(pending)}")
                self.host.sleep(3)
        if daemons:
            self.host.sleep(self.host.stabilize_seconds)
        for spec in daemons:
            state = self.host.inspect(self.host.container(spec["name"]) or "")
            if (state["State"]["Status"] != "running"
                    or state.get("RestartCount", 0) != restarts.get(spec["name"], 0)):
                raise DeployError(f"{spec['name']} did not stay up")

    # public operations

    def deploy(self, component: str, version: str, image: str, bundle: Path) -> Outcome:
        validate_arguments(component, version, image)
        # Before the platform bootstrap, the running stack is the legacy monolith model;
        # a component deploy would model only itself. The platform release seeds all.
        if not (self.host.releases("platform") / "current").exists():
            raise DeployError("no platform release is installed; run deploy-platform.sh first")
        outcome = Outcome(component, version)
        with self.host.lock(self.host.lock_path), tempfile.TemporaryDirectory() as work:
            staged = Path(work)
            extract_bundle(bundle, staged)
            release = load_release(component, version, image, staged)
            record = self.host.releases(component) / version
            if (record / "result").exists():
                raise DeployError(f"{component} {version} was already deployed; "
                                  "use rollback --to to redeploy a recorded version")
            outcome.previous = self.current_version(component) or "none"
            self.preflight(release)

            # record what is live now, then activate
            record.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged, record / "bundle", dirs_exist_ok=True)
            previous_pin = read_env(self.host.images_env).get(release.image_var, "")
            live = self.host.components / component
            write_atomic(record / "previous.json", json.dumps({
                "version": outcome.previous, "image": previous_pin,
                "manifest": self.installed_manifest(component)}, indent=1))
            if (live / "compose.yaml").exists():
                shutil.copy(live / "compose.yaml", record / "previous-compose.yaml")
            write_atomic(record / "release.json", json.dumps({
                "component": component, "version": version, "image": image,
                "started_at": _now()}, indent=1))

            try:
                self._activate(release, staged)
                self.fix_ownership(release)
                self.roll(release)
                self.verify(release, image)
            except DeployError as exc:
                self.log(f"deploy failed: {exc}")
                outcome.error = str(exc)
                outcome.rollback = self._recover(release, record)
                write_atomic(record / "result", f"{outcome.rollback}\n")
                self._history(component, version, outcome.rollback)
                return outcome
            outcome.verification = "succeeded"
            write_atomic(record / "result", "succeeded\n")
            write_atomic(record / "activated-at", _now() + "\n")
            write_atomic(self.host.releases(component) / "current", version + "\n")
            self._history(component, version, "succeeded")
            self.log(f"{component} {version} is live")
            return outcome

    def _history(self, component: str, version: str, result: str) -> None:
        path = self.host.releases(component) / "history.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()} {version} {result}\n")

    def _activate(self, release: Release, staged: Path) -> None:
        live = self.host.components / release.component
        live.mkdir(parents=True, exist_ok=True)
        write_atomic(live / "compose.yaml", (staged / "compose.yaml").read_text("utf-8"))
        write_atomic(live / "component.json", (staged / "component.json").read_text("utf-8"))
        set_pin(self.host.images_env, release.image_var, release.image)

    def _recover(self, release: Release, record: Path) -> str:
        rollback_class = release.manifest["platform"]["rollback"]
        previous = json.loads((record / "previous.json").read_text(encoding="utf-8"))
        if rollback_class == "forward-only":
            return "failed-forward-only"
        old_contract = ((previous.get("manifest") or {}).get("platform") or {}).get(
            "data_contract", release.manifest["platform"].get("data_contract"))
        if rollback_class == "writer" and old_contract != release.manifest["platform"].get(
                "data_contract"):
            for spec in self.enabled(release.services):
                self.host.compose("stop", spec["name"], check=False)
            return "fix-forward-required"
        if not previous.get("image"):
            for spec in self.enabled(release.services):
                self.host.compose("stop", spec["name"], check=False)
            return "stopped-no-previous-release"
        try:
            live = self.host.components / release.component
            if (record / "previous-compose.yaml").exists():
                shutil.copy(record / "previous-compose.yaml", live / "compose.yaml")
            if previous.get("manifest"):
                write_atomic(live / "component.json", json.dumps(previous["manifest"], indent=1))
            set_pin(self.host.images_env, release.image_var, previous["image"])
            self.roll(release)
            self.verify(release, previous["image"])
        except DeployError as exc:
            self.log(f"rollback failed: {exc}")
            return "rollback-failed"
        return "rolled-back"

    def rollback(self, component: str, to: str | None = None) -> Outcome:
        releases = self.host.releases(component)
        if to is None:
            current = self.current_version(component)
            if not current:
                raise DeployError(f"{component} has no current release to roll back from")
            to = json.loads((releases / current / "previous.json").read_text("utf-8"))["version"]
        record = releases / to
        if not (record / "bundle" / "component.json").exists():
            raise DeployError(f"no recorded bundle for {component} {to}")
        image = json.loads((record / "release.json").read_text("utf-8"))["image"]
        with tempfile.TemporaryDirectory() as work:
            bundle = Path(work) / "bundle.tgz"
            with tarfile.open(bundle, "w:gz") as archive:
                for path in (record / "bundle").iterdir():
                    archive.add(path, arcname=path.name)
            (record / "result").unlink(missing_ok=True)  # history.log keeps the earlier outcome
            return self.deploy(component, to, image, bundle)

    def drift(self) -> list[str]:
        """Services whose running container no longer matches the Compose model.

        Compose recreates such a service on its next ``up``; a platform release reports
        them instead of recreating anything (ClickHouse and the IB Gateways are only
        recreated deliberately, in a maintenance window).
        """
        changed = []
        for line in self.host.compose("config", "--hash", "*").stdout.splitlines():
            name, _, digest = line.strip().partition(" ")
            container = self.host.container(name) if name else None
            if not container:
                continue
            labels = self.host.inspect(container).get("Config", {}).get("Labels") or {}
            if labels.get("com.docker.compose.config-hash") != digest:
                changed.append(name)
        return changed

    def platform(self, version: str, bundle: Path) -> Outcome:
        """Install a platform bundle (compose base, host scripts and tools) in place.

        Keeps production.env, never overwrites component-managed fragments, runs
        prepare-host.sh, and recreates nothing. Drifted services are reported.
        """
        if not VERSION.match(version):
            raise DeployError(f"invalid version {version!r}")
        outcome = Outcome("platform", version)
        releases = self.host.releases("platform")
        with self.host.lock(self.host.lock_path), tempfile.TemporaryDirectory() as work:
            staged = Path(work)
            extract_bundle(bundle, staged)
            for required in ("deploy/compose.base.yml", "deploy/host/factorlab_deploy.py",
                             "deploy/scripts/prepare-host.sh"):
                if not (staged / required).is_file():
                    raise DeployError(f"platform bundle is missing {required}")
            record = releases / version
            if (record / "result").exists():
                raise DeployError(f"platform {version} was already deployed")
            outcome.previous = self.current_version("platform") or "none"
            record.mkdir(parents=True, exist_ok=True)
            live, fresh, old = (self.host.platform, self.host.root / ".deploy-new",
                                self.host.root / ".deploy-old")
            shutil.rmtree(fresh, ignore_errors=True)
            shutil.copytree(staged / "deploy", fresh)
            for keep in ("production.env",):
                if (live / keep).exists():
                    shutil.copy2(live / keep, fresh / keep)
            shutil.rmtree(record / "previous-deploy", ignore_errors=True)
            if live.exists():
                shutil.copytree(live, record / "previous-deploy", symlinks=True)
                shutil.rmtree(old, ignore_errors=True)
                live.rename(old)
            fresh.rename(live)
            try:
                if (staged / "components").is_dir() and (self.host.root / "current-image").exists():
                    monolith = (self.host.root / "current-image").read_text("utf-8").strip()
                    self._seed_components(staged, monolith)
                prepared = self.host.run(["bash", str(live / "scripts" / "prepare-host.sh")])
                if prepared.returncode:
                    raise DeployError(f"prepare-host.sh failed: {prepared.stderr.strip()[:500]}")
                self.host.compose("config", "--quiet")
            except DeployError as exc:
                self.log(f"platform deploy failed, restoring {outcome.previous}: {exc}")
                shutil.rmtree(live, ignore_errors=True)
                if old.exists():
                    old.rename(live)
                outcome.error, outcome.rollback = str(exc), "rolled-back"
                write_atomic(record / "result", "rolled-back\n")
                self._history("platform", version, "rolled-back")
                return outcome
            shutil.rmtree(old, ignore_errors=True)
            drifted = self.drift()
            write_atomic(record / "drift", "".join(f"{name}\n" for name in drifted))
            if drifted:
                self.log("services that differ from the model (recreated on their next release "
                         f"or maintenance window): {', '.join(drifted)}")
            outcome.verification = "succeeded"
            write_atomic(record / "result", "succeeded\n")
            write_atomic(releases / "current", version + "\n")
            self._history("platform", version, "succeeded")
            return outcome

    def _seed_components(self, platform_bundle: Path, image: str) -> None:
        if not re.fullmatch(re.escape(f"{REGISTRY}/factorlab") + DIGEST, image):
            return  # no monolith to bridge from; component releases pin their own images
        for source in sorted((platform_bundle / "components").glob("*/component.json")):
            name = source.parent.name
            live = self.host.components / name
            if (live / "compose.yaml").exists():
                continue  # component-managed; never overwrite
            live.mkdir(parents=True, exist_ok=True)
            shutil.copy(source.parent / "compose.yaml", live / "compose.yaml")
            shutil.copy(source, live / "component.json")
            variable = json.loads(source.read_text("utf-8"))["platform"]["image_var"]
            if not read_env(self.host.images_env).get(variable):
                set_pin(self.host.images_env, variable, image)
            self.log(f"seeded {name} on the monolith image")

    def seed(self, platform_bundle: Path, image: str) -> None:
        """Bridge: install every component's fragment from the platform bundle, pinned to
        the monolith image, without recreating anything that already runs it."""
        if not re.fullmatch(re.escape(f"{REGISTRY}/factorlab") + DIGEST, image):
            raise DeployError("seed takes the monolith image digest")
        with self.host.lock(self.host.lock_path):
            self._seed_components(platform_bundle, image)
            self.host.compose("config", "--quiet")

    def status(self) -> list[str]:
        pins = read_env(self.host.images_env)
        rows = []
        for path in sorted(self.host.components.glob("*/component.json")):
            manifest = json.loads(path.read_text("utf-8"))
            image = pins.get(manifest["platform"]["image_var"], "-")
            rows.append(f"{manifest['name']:18} {self.current_version(manifest['name']) or 'seeded':10}"
                        f" {image[-19:]}")
        return rows


def _now() -> str:
    # timezone.utc, not datetime.UTC: the host python3 may predate 3.11.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factorlab_deploy.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser("deploy")
    deploy.add_argument("component")
    deploy.add_argument("version")
    deploy.add_argument("image")
    deploy.add_argument("bundle", type=Path)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("component")
    rollback.add_argument("--to")
    platform = commands.add_parser("platform")
    platform.add_argument("version")
    platform.add_argument("bundle", type=Path)
    seed = commands.add_parser("seed")
    seed.add_argument("platform_bundle", type=Path)
    seed.add_argument("image")
    commands.add_parser("status")
    compose = commands.add_parser("compose")
    compose.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    host = Host()
    deployer = Deployer(host)
    try:
        if args.command == "compose":
            passthrough = args.args[1:] if args.args[:1] == ["--"] else args.args
            # Replace this process so `logs -f`, `exec` and TTYs behave as plain compose.
            os.execvp("docker", ["docker", *host.compose_args(*passthrough)])
        if args.command == "status":
            print("\n".join(deployer.status()))
            return 0
        if args.command == "seed":
            deployer.seed(args.platform_bundle, args.image)
            return 0
        if args.command == "platform":
            outcome = deployer.platform(args.version, args.bundle)
        elif args.command == "deploy":
            outcome = deployer.deploy(args.component, args.version, args.image, args.bundle)
        else:
            outcome = deployer.rollback(args.component, args.to)
        if outcome.error:
            print(f"error: {outcome.error}", file=sys.stderr)
        print("\n".join(outcome.lines()))
        return 0 if outcome.verification == "succeeded" else 1
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.command in {"deploy", "rollback", "platform"}:
            print(f"FACTORLAB_RESULT_COMPONENT={getattr(args, 'component', 'platform')}")
            print("FACTORLAB_RESULT_VERIFICATION=failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
