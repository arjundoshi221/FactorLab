"""factorlab-healthcheck probes."""

from __future__ import annotations

import os
import time

from factorlab.core import healthcheck, paths


def test_heartbeat_fresh_stale_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HEARTBEAT_ROOT", tmp_path)
    assert healthcheck.main(["heartbeat", "ingest-us"]) == 1
    beat = tmp_path / "ingest-us"
    beat.write_text("", encoding="utf-8")
    assert healthcheck.main(["heartbeat", "ingest-us", "--max-age", "60"]) == 0
    old = time.time() - 600
    os.utime(beat, (old, old))
    assert healthcheck.main(["heartbeat", "ingest-us", "--max-age", "60"]) == 1


def test_files_exist_and_are_recent(tmp_path):
    marker = tmp_path / ".agent-ready"
    assert healthcheck.main(["files", str(marker)]) == 1
    marker.write_text("x", encoding="utf-8")
    assert healthcheck.main(["files", str(marker), "--max-age", "180"]) == 0


def test_http_failure_is_unhealthy():
    assert healthcheck.main(["http", "http://127.0.0.1:9/health", "--timeout", "0.5"]) == 1
