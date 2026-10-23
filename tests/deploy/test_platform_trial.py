"""The platform trial replaces one platform service, checks it, and restores on failure."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

if sys.platform == "win32":
    sys.modules.setdefault("fcntl", ModuleType("fcntl"))

RUNNER = Path(__file__).resolve().parents[2] / "deploy" / "testing"
sys.path.insert(0, str(RUNNER))
spec = importlib.util.spec_from_file_location("platform_trial", RUNNER / "platform_trial.py")
assert spec and spec.loader
platform_trial = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = platform_trial
spec.loader.exec_module(platform_trial)
test = platform_trial.test

COMMIT = "a" * 40
IMAGE_ID = "sha256:" + "c" * 64
OLD_ID = "sha256:" + "d" * 64


def state(name, image):
    return {"name": "/" + name, "id": name, "image": image, "started": "t", "running": True,
            "restarts": 0}  # fmt: skip


class Host:
    """A fake docker host: compose models, container identities and health."""

    def __init__(self, root: Path, service: str):
        self.root, self.service, self.calls = root, service, []
        self.containers = {
            "factorlab-clickhouse": state("factorlab-clickhouse", "ch"),
            "factorlab-api-1": state("factorlab-api-1", "api"),
            f"factorlab-{service}-1": state(f"factorlab-{service}-1", OLD_ID),
        }
        if service != "monitor":
            self.containers["factorlab-monitor-1"] = state("factorlab-monitor-1", "mon")
        self.model = {
            "services": {
                service: {"image": "ghcr.io/x:pinned", "environment": {"LIVE": "1"}},
                "api": {"image": "api"},
            },
            "volumes": {},
        }
        self.newer = {
            "services": {
                service: {
                    "image": "ghcr.io/x:new",
                    "environment": {"LIVE": "new", "ADDED": "1"},
                    "volumes": [{"type": "volume", "source": "fresh_runtime", "target": "/r"}],
                }
            },
            "volumes": {"fresh_runtime": {"driver_opts": {"type": "tmpfs"}}},
        }
        self.fail_up = False
        self.ready = 100.0

    def run(self, *args, capture=True):
        self.calls.append(args)
        if args[:2] == ("docker", "ps"):
            return "\n".join(self.containers)
        if args[:2] == ("docker", "exec"):
            return str(self.ready)
        return ""

    def compose(self, path, *args):
        self.calls.append(("compose", str(path), *args))
        if args[:1] == ("config",) and "--quiet" not in args:
            source = self.newer if "source" in str(path) else self.model
            return json.dumps(source)
        if args[:1] == ("up",):
            if self.fail_up and "candidate" in self.installed:
                raise RuntimeError("container exited")
            image = json.loads(Path(self.installed).read_text())["services"][self.service]["image"]
            self.containers[f"factorlab-{self.service}-1"] = state(
                f"factorlab-{self.service}-1", image
            )
            self.ready += 1
        return "ok\n"

    def public_state(self, names):
        return [self.containers[n] for n in names]

    def inspect(self, names):
        return [
            {"State": {"Running": True, "Health": {"Status": "healthy"}}, "RestartCount": 0,
             "Image": self.containers[n]["image"]}
            for n in names
        ]  # fmt: skip

    def install(self, model):
        self.installed = str(model)


@pytest.fixture
def host(monkeypatch, tmp_path, request):
    scope = platform_trial.SCOPES[request.param]
    fake = Host(tmp_path, scope.service)
    fake.installed = "live"
    monkeypatch.setattr(test, "run", fake.run)
    monkeypatch.setattr(test, "compose", fake.compose)
    monkeypatch.setattr(test, "public_state", fake.public_state)
    monkeypatch.setattr(test, "inspect", fake.inspect)
    monkeypatch.setattr(test, "install_model", fake.install)
    monkeypatch.setattr(test, "DEPLOY", tmp_path / "deploy")
    monkeypatch.setattr(platform_trial, "monitor_online", lambda: True)
    monkeypatch.setattr(platform_trial.time, "sleep", lambda _: None)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(platform_trial.urllib.request, "urlopen", lambda *a, **k: Response())
    (tmp_path / "source/deploy").mkdir(parents=True)
    return scope, fake


@pytest.mark.parametrize("host", ["monitor", "secrets-agent"], indirect=True)
def test_a_long_running_service_is_replaced_and_verified(host, tmp_path):
    scope, fake = host
    platform_trial.plan(tmp_path, scope, IMAGE_ID)
    rollback = json.loads((tmp_path / "rollback.compose.json").read_text())
    candidate = json.loads((tmp_path / "candidate.compose.json").read_text())
    # Rollback pins the running image; the candidate keeps live settings and adds new ones.
    assert rollback["services"][scope.service]["image"] == OLD_ID
    definition = candidate["services"][scope.service]
    assert definition["image"] == IMAGE_ID
    assert definition["environment"] == {"LIVE": "1", "ADDED": "1"}
    assert "fresh_runtime" in candidate["volumes"]
    assert ("docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
            IMAGE_ID, "-c", f"import {scope.module}") in fake.calls  # fmt: skip

    platform_trial.apply(tmp_path, scope)

    assert fake.containers[f"factorlab-{scope.service}-1"]["image"] == IMAGE_ID
    assert (tmp_path / "applied").exists() and not (tmp_path / "restored").exists()
    ups = [c for c in fake.calls if c[0] == "compose" and c[2] == "up"]
    assert ups == [("compose", str(test.DEPLOY / "compose.production.yml"), "up", "-d",
                    "--no-deps", "--force-recreate", scope.service)]  # fmt: skip


@pytest.mark.parametrize("host", ["secrets-agent"], indirect=True)
def test_an_agent_that_never_syncs_is_rolled_back(host, tmp_path, monkeypatch):
    scope, fake = host
    platform_trial.plan(tmp_path, scope, IMAGE_ID)
    monkeypatch.setattr(platform_trial, "HEALTH_TIMEOUT", 0)
    real_up = fake.compose

    def compose(path, *args):
        result = real_up(path, *args)
        if args[:1] == ("up",) and "candidate" in fake.installed:
            fake.ready -= 1  # the new container never rewrites .agent-ready
        return result

    monkeypatch.setattr(test, "compose", compose)
    with pytest.raises(RuntimeError, match="fresh secrets-agent sync"):
        platform_trial.apply(tmp_path, scope)
    assert fake.installed.endswith("rollback.compose.json")
    assert fake.containers["factorlab-cloudflare-secrets-agent-1"]["image"] == OLD_ID
    assert (tmp_path / "restored").exists() and not (tmp_path / "applied").exists()


@pytest.mark.parametrize("host", ["monitor"], indirect=True)
def test_a_changed_protected_container_fails_the_trial(host, tmp_path):
    scope, fake = host
    platform_trial.plan(tmp_path, scope, IMAGE_ID)
    fake.containers["factorlab-api-1"] = state("factorlab-api-1", "other")
    with pytest.raises(RuntimeError, match="protected"):
        platform_trial.apply(tmp_path, scope)
    assert not (tmp_path / "applied").exists()


@pytest.mark.parametrize("host", ["monitor"], indirect=True)
def test_a_service_changed_since_plan_is_not_replaced(host, tmp_path):
    scope, fake = host
    platform_trial.plan(tmp_path, scope, IMAGE_ID)
    fake.containers["factorlab-monitor-1"] = state("factorlab-monitor-1", "manual")
    with pytest.raises(RuntimeError, match="changed since plan"):
        platform_trial.apply(tmp_path, scope)
    assert not any(c[0] == "compose" and c[2] == "up" for c in fake.calls)


@pytest.mark.parametrize("host", ["schema-migrator"], indirect=True)
def test_the_migrator_only_runs_read_only_checks_before_it_is_pinned(host, tmp_path):
    scope, fake = host
    platform_trial.plan(tmp_path, scope, IMAGE_ID)
    platform_trial.apply(tmp_path, scope)
    candidate = str(tmp_path / "candidate.compose.json")
    runs = [c[2:] for c in fake.calls if c[0] == "compose" and c[2] == "run"]
    assert runs == [
        ("run", "--rm", "--no-deps", "bootstrap"),
        ("run", "--rm", "--no-deps", "bootstrap", "migrate", "status"),
    ]
    assert all(c[1] == candidate for c in fake.calls if c[0] == "compose" and c[2] == "run")
    assert not any(c[0] == "compose" and c[2] == "up" for c in fake.calls)
    assert not any("apply" in c for c in fake.calls)
    assert fake.installed == candidate and (tmp_path / "applied").exists()


@pytest.mark.parametrize("host", ["schema-migrator"], indirect=True)
def test_a_failing_readiness_check_leaves_the_old_migrator(host, tmp_path, monkeypatch):
    scope, fake = host
    platform_trial.plan(tmp_path, scope, IMAGE_ID)
    real = fake.compose

    def compose(path, *args):
        if args[:1] == ("run",):
            raise RuntimeError("docker exited 1; see private error log")
        return real(path, *args)

    monkeypatch.setattr(test, "compose", compose)
    with pytest.raises(RuntimeError):
        platform_trial.apply(tmp_path, scope)
    assert fake.installed.endswith("rollback.compose.json")


def test_scopes_use_each_components_runtime_user_and_service():
    import yaml

    for name, scope in platform_trial.SCOPES.items():
        manifest = yaml.safe_load(
            (RUNNER.parents[1] / "components" / name / "component.yaml").read_text("utf-8")
        )
        assert manifest["platform"]["user"] == scope.user
        assert scope.service in [s["name"] for s in manifest["services"]]
        assert scope.unit == manifest["name"]


def test_the_workflow_accepts_exactly_the_runner_scopes():
    workflow = (RUNNER.parents[1] / ".github/workflows/branch-platform-deploy.yml").read_text(
        "utf-8"
    )
    assert "^platform-trial/(monitor|secrets-agent|schema-migrator)/" in workflow
    assert set(platform_trial.SCOPES) == {"monitor", "secrets-agent", "schema-migrator"}
    for runner in platform_trial.RUNNERS:
        assert f"deploy/testing/{runner}" in workflow
    assert "environment: production" in workflow
