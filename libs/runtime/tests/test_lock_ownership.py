"""Locks must exclude live workers and recover immediately after process death."""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

from factorlab.runtime import acquire_lock


def test_live_owner_cannot_be_stolen_or_have_its_record_removed(tmp_path):
    path = tmp_path / "worker.lock"
    with acquire_lock(path):
        body = json.loads(path.read_text())
        body["started_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        path.write_text(json.dumps(body))
        with (
            pytest.raises(SystemExit, match="another orchestrator"),
            acquire_lock(path, stale_seconds=1),
        ):
            pytest.fail("a live lock was stolen")
        assert path.exists()
    assert not path.exists()


def test_process_crash_releases_lock_without_waiting_for_stale_timeout(tmp_path):
    path = tmp_path / "worker.lock"
    ready = tmp_path / "ready"
    script = """
import sys, time
from pathlib import Path
from factorlab.runtime import acquire_lock
with acquire_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).touch()
    time.sleep(60)
"""
    worker = subprocess.Popen([sys.executable, "-c", script, str(path), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and worker.poll() is None:
            time.sleep(0.02)
        assert ready.exists(), "worker did not acquire its lock"
        with pytest.raises(SystemExit, match="another orchestrator"), acquire_lock(path):
            pytest.fail("another process acquired the worker's lock")
        worker.kill()
        worker.wait(timeout=10)
        assert path.exists(), "crash should leave the ownership record"
        with acquire_lock(path):
            assert json.loads(path.read_text())["pid"] != worker.pid
        assert not path.exists()
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
