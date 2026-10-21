"""A component trial must not replace other ingestion writers."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def runner(monkeypatch):
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "fcntl", ModuleType("fcntl"))
    path = Path(__file__).resolve().parents[2] / "deploy/testing/ingestion_test.py"
    spec = importlib.util.spec_from_file_location("ingestion_test_scope", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_us_scope_persists_and_protects_other_writers(runner, tmp_path):
    runner.configure_scope(tmp_path, "ingest-us", planning=True)
    assert runner.DAEMONS == ["ingest-us", "universe-us"]
    assert set(runner.SERVICES.values()) == {"ingest-us"}
    assert "factorlab-ingest-india-1" in runner.PROTECTED
    assert "factorlab-ibkr-snapshot-1" in runner.PROTECTED
    assert "factorlab-universe-us-1" not in runner.PROTECTED
    runner.configure_scope(tmp_path, None, planning=False)
    assert runner.DAEMONS == ["ingest-us", "universe-us"]
    with pytest.raises(RuntimeError, match="differs"):
        runner.configure_scope(tmp_path, "ingest-india", planning=False)


def test_service_scope_adds_one_service_and_protects_its_unit(runner, tmp_path):
    runner.configure_scope(tmp_path, "futures-us", planning=True)
    assert runner.SERVICES == {"futures-us": "ingest-us"}
    assert runner.DAEMONS == ["futures-us"]
    assert "factorlab-ingest-us-1" in runner.PROTECTED
    assert "factorlab-universe-us-1" in runner.PROTECTED
    assert "factorlab-futures-us-1" not in runner.PROTECTED
    # The new service is absent from the live model: nothing to stop or pin for rollback.
    assert runner.live({"services": {"ingest-us": {}, "universe-us": {}}}) == []
    assert runner.live({"services": {"futures-us": {}}}) == ["futures-us"]
    with pytest.raises(RuntimeError, match="differs"):
        runner.configure_scope(tmp_path, "ingest-us", planning=False)


def test_restore_removes_services_the_trial_added(runner, tmp_path, monkeypatch):
    runner.configure_scope(tmp_path, "futures-us", planning=True)
    (tmp_path / "rollback.compose.json").write_text('{"services": {"ingest-us": {}}}')
    (tmp_path / "protected-containers.json").write_text("[]")
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", lambda args, **_: calls.append(args))
    monkeypatch.setattr(runner, "install_model", lambda path: calls.append(("install", path.name)))
    monkeypatch.setattr(runner, "compose", lambda *args: calls.append(("compose", *args)))
    monkeypatch.setattr(runner, "verify_protected", lambda before: None)
    runner.restore(tmp_path)
    assert calls == [
        ("docker", "rm", "-f", "factorlab-futures-us-1"),
        ("install", "rollback.compose.json"),
    ]
    assert (tmp_path / "restored").exists()


def test_legacy_scope_is_preserved_and_cannot_be_narrowed(runner, tmp_path):
    runner.configure_scope(tmp_path, None, planning=False)
    assert set(runner.SERVICES) == set(runner.ALL_SERVICES)
    assert runner.PROTECTED == runner.BASE_PROTECTED
    with pytest.raises(RuntimeError, match="unscoped"):
        runner.configure_scope(tmp_path, "ingest-us", planning=False)


def test_unknown_scope_is_refused(runner, tmp_path):
    with pytest.raises(RuntimeError, match="Unknown"):
        runner.configure_scope(tmp_path, "api", planning=True)
    assert not (tmp_path / "scope.json").exists()
