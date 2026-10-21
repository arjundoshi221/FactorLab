"""The ingestion trial checks the image, then runs the reviewed plan/apply/verify for one scope."""

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
spec = importlib.util.spec_from_file_location("ingest_trial", RUNNER / "ingest_trial.py")
assert spec and spec.loader
ingest_trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest_trial)

COMMIT = "a" * 40
TEST_ID = "20261021-" + COMMIT[:12]
IMAGE = "ghcr.io/arjundoshi221/factorlab-ingest-us@sha256:" + "b" * 64
IMAGE_ID = "sha256:" + "c" * 64


def metadata(**labels):
    base = {
        "io.factorlab.component": "ingest-us",
        "org.opencontainers.image.revision": COMMIT,
        "org.opencontainers.image.version": f"0.0.0-trial.{COMMIT[:12]}",
    }
    return [{"Id": IMAGE_ID, "Config": {"User": "10001:10001", "Labels": base | labels}}]


def docker(monkeypatch, inspected):
    calls = []

    def run(*args, capture=True):
        calls.append(args)
        return json.dumps(inspected) if args[:3] == ("docker", "image", "inspect") else ""

    monkeypatch.setattr(ingest_trial.test, "run", run)
    return calls


def test_image_is_pulled_checked_and_tagged_for_the_runner(monkeypatch):
    calls = docker(monkeypatch, metadata())
    assert ingest_trial.install_image(IMAGE, "ingest-us", COMMIT, TEST_ID) == IMAGE_ID
    assert calls[0] == ("docker", "pull", IMAGE)
    assert calls[-1] == ("docker", "tag", IMAGE_ID, f"factorlab-test-ingest-us:{TEST_ID}")


@pytest.mark.parametrize(
    ("image", "labels"),
    [
        (IMAGE.replace("ingest-us", "ingest-india"), {}),
        (IMAGE, {"org.opencontainers.image.revision": "f" * 40}),
        (IMAGE, {"io.factorlab.component": "api"}),
    ],
)
def test_wrong_image_is_refused(monkeypatch, image, labels):
    docker(monkeypatch, metadata(**labels))
    with pytest.raises(RuntimeError):
        ingest_trial.install_image(image, "ingest-us", COMMIT, TEST_ID)


def harness(monkeypatch, tmp_path, *, apply_error=None):
    deploy = tmp_path / "opt" / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "compose.production.yml").write_text("services: {}\n")
    model = tmp_path / "candidate.yml"
    model.write_text("services: {}\n")
    steps = []
    monkeypatch.setattr(ingest_trial.test, "DEPLOY", deploy)
    monkeypatch.setattr(ingest_trial.test, "BASE", tmp_path / "tests")
    monkeypatch.setattr(
        ingest_trial, "install_image", lambda *args: steps.append(("image", *args[1:]))
    )
    monkeypatch.setattr(ingest_trial.test, "plan", lambda root, test_id: steps.append("plan"))

    def apply(root):
        steps.append("apply")
        if apply_error:
            raise apply_error
        (root / "after-containers.json").write_text(json.dumps([{"name": "/factorlab-x-1"}]))

    monkeypatch.setattr(ingest_trial.test, "apply", apply)
    return model, steps


def test_apply_plans_then_applies_one_scope(monkeypatch, tmp_path, capsys):
    model, steps = harness(monkeypatch, tmp_path)
    root = tmp_path / "tests" / TEST_ID
    ingest_trial.apply(root, TEST_ID, "futures-us", COMMIT, IMAGE, model)
    assert steps == [("image", "ingest-us", COMMIT, TEST_ID), "plan", "apply"]
    assert json.loads((root / "scope.json").read_text()) == {"component": "futures-us"}
    assert (root / "backup/deploy/compose.production.yml").is_file()
    assert (root / "source/deploy/compose.production.yml").read_text() == "services: {}\n"
    out = capsys.readouterr().out
    assert "FACTORLAB_RESULT_VERIFICATION=passed" in out
    assert "FACTORLAB_RESULT_ROLLBACK=not-needed" in out
    with pytest.raises(RuntimeError, match="already exists"):
        ingest_trial.apply(root, TEST_ID, "futures-us", COMMIT, IMAGE, model)


def test_failed_apply_reports_the_restore(monkeypatch, tmp_path, capsys):
    model, _ = harness(monkeypatch, tmp_path, apply_error=RuntimeError("Unstable service"))
    with pytest.raises(RuntimeError, match="Unstable"):
        ingest_trial.apply(tmp_path / "tests" / TEST_ID, TEST_ID, "ingest-us", COMMIT, IMAGE, model)
    out = capsys.readouterr().out
    assert "FACTORLAB_RESULT_ROLLBACK=restored" in out
    assert "VERIFICATION=passed" not in out


def test_component_pins_hand_over_to_normal_releases(monkeypatch, tmp_path):
    model, steps = harness(monkeypatch, tmp_path)
    (tmp_path / "opt" / "state").mkdir()
    (tmp_path / "opt" / "state" / "images.env").write_text("")
    with pytest.raises(RuntimeError, match="normal release"):
        ingest_trial.apply(tmp_path / "tests" / TEST_ID, TEST_ID, "ingest-us", COMMIT, IMAGE, model)
    assert steps == []
