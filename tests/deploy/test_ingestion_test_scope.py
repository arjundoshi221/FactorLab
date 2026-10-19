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
