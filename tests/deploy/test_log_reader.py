"""deploy/host/factorlab_log_reader.py: bounded, redacted, read-only log access."""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("factorlab_log_reader",
                                              REPO / "deploy/host/factorlab_log_reader.py")
reader = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reader
spec.loader.exec_module(reader)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _record(hours_ago: float, msg: str, level: str = "INFO", **extra) -> str:
    return json.dumps({"ts": _ts(hours_ago), "level": level, "component": "api",
                       "service": "api", "logger": "factorlab.api", "msg": msg, **extra})


def _touch(path: Path, hours_ago: float) -> None:
    moment = (NOW - timedelta(hours=hours_ago)).timestamp()
    os.utime(path, (moment, moment))


@pytest.fixture
def root(tmp_path):
    logs = tmp_path / "log"
    api = logs / "api"
    api.mkdir(parents=True)
    with gzip.open(api / "api.jsonl-20260927-10.gz", "wt", encoding="utf-8") as old:
        old.write(_record(50, "rotated long ago") + "\n")
        old.write(_record(30, "rotated yesterday", level="ERROR",
                          exc="Traceback (most recent call last):\nValueError: boom 7") + "\n")
    _touch(api / "api.jsonl-20260927-10.gz", 29)
    (api / "api.jsonl").write_text("\n".join([
        _record(5, "GET /hub/api/v1/overview 200", route="/hub/api/v1/overview"),
        _record(4, "upstream failed after 3 retries", level="ERROR", run_id="run-1",
                exc="Traceback (most recent call last):\n  ...\nTimeoutError: took 30s"),
        _record(3, "upstream failed after 5 retries", level="ERROR", run_id="run-2"),
        _record(2, "calling https://x.example/?access_token=SECRET123&a=1",
                api_key="SECRET456"),
        _record(1, "shutting down", level="WARNING"),
    ]) + "\n", encoding="utf-8")
    _touch(api / "api.jsonl", 1)
    political = logs / "ingest-political"
    political.mkdir()
    (political / "cron.log").write_text("plain cron line\n", encoding="utf-8")
    _touch(political / "cron.log", 2)
    with gzip.open(political / "cron.log-legacy.gz", "wt", encoding="utf-8") as legacy:
        legacy.write("legacy cron line\n")
    _touch(political / "cron.log-legacy.gz", 3)
    (political / "ingest-political.jsonl").write_text(json.dumps({
        "ts": _ts(4), "level": "INFO", "component": "ingest-political",
        "service": "ingest-political", "logger": "x", "msg": "run started",
        "run_id": "run-1"}) + "\n", encoding="utf-8")
    _touch(political / "ingest-political.jsonl", 4)
    (logs / "not a component").mkdir()
    (api / "notes.txt").write_text("ignored\n", encoding="utf-8")
    return logs


def run(root: Path, *argv: str, snapshots=()):
    return reader.execute(list(argv), root=root, now=NOW, snapshots=snapshots)


def msgs(items):
    return [item["msg"] for item in items]


def test_tail_returns_the_newest_matching_records_in_order(root):
    items, meta = run(root, "tail", "--component", "api", "--lines", "2")
    assert msgs(items) == ["calling https://x.example/?access_token=[redacted]&a=1",
                           "shutting down"]
    assert meta["matched"] == 5
    assert meta["since"].startswith("2026-09-28T12:00")


def test_tail_reads_rotated_files_inside_the_window(root):
    items, _ = run(root, "tail", "--component", "api", "--since", "40h")
    assert msgs(items)[0] == "rotated yesterday"
    assert "rotated long ago" not in msgs(items)


def test_filters_level_grep_regex_and_run_id(root):
    assert msgs(run(root, "tail", "--component", "api", "--level", "error")[0]) == [
        "upstream failed after 3 retries", "upstream failed after 5 retries"]
    assert len(run(root, "tail", "--component", "api", "--grep", "OVERVIEW")[0]) == 1
    assert len(run(root, "tail", "--component", "api", "--regex", r"after \d retries")[0]) == 2
    assert msgs(run(root, "tail", "--component", "api", "--run-id", "run-2")[0]) == [
        "upstream failed after 5 retries"]


def test_tracebacks_are_summarized_unless_asked_for(root):
    [first, _] = run(root, "tail", "--component", "api", "--level", "ERROR")[0]
    assert first["exc_summary"] == "TimeoutError: took 30s" and "exc" not in first
    [full, _] = run(root, "tail", "--component", "api", "--level", "ERROR", "--exc")[0]
    assert full["exc"].startswith("Traceback")


def test_credentials_are_redacted_by_the_reader(root):
    [item] = run(root, "tail", "--component", "api", "--grep", "calling")[0]
    assert "SECRET123" not in json.dumps(item) and item["api_key"] == "[redacted]"
    assert "access_token=[redacted]" in item["msg"]


def test_plain_text_logs_are_read_as_raw_records(root):
    items, _ = run(root, "tail", "--component", "ingest-political", "--service", "cron")
    assert msgs(items) == ["legacy cron line", "plain cron line"]
    assert all(item["raw"] for item in items)


def test_errors_are_grouped_by_fingerprint(root):
    groups, meta = run(root, "errors", "--since", "2d")
    assert meta["errors"] == 3
    top = groups[0]
    assert (top["count"], top["fingerprint"]) == (2, "upstream failed after <n> retries")
    assert top["exc_summary"] == "TimeoutError: took 30s"


def test_run_collects_one_run_across_components(root):
    items, _ = run(root, "run", "--run-id", "run-1")
    assert {(i["component"], i["msg"]) for i in items} == {
        ("api", "upstream failed after 3 retries"), ("ingest-political", "run started")}


def test_list_shows_components_files_and_running_containers(root, tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"snapshot_at": "2026-09-29T11:59:00Z", "images": [{
        "labels": {"title": "factorlab-api", "version": "1.2.0"},
        "containers": [{"service": "api", "status": "running", "started_at": "x"}]}]}))
    items, _ = run(root, "list", snapshots=(tmp_path / "missing.json", snapshot))
    components = {item["component"]: item for item in items if "component" in item}
    assert set(components) == {"api", "ingest-political"}
    assert [f["file"] for f in components["api"]["files"]] == [
        "api.jsonl-20260927-10.gz", "api.jsonl"]
    [containers] = [item for item in items if "containers" in item]
    assert containers["containers"][0]["version"] == "1.2.0"


@pytest.mark.parametrize("argv", [
    ["tail", "--component", "../etc"],
    ["tail", "--component", "api", "--service", "../../passwd"],
    ["tail", "--component", "missing"],
    ["tail", "--component", "api", "--since", "45d"],
    ["tail", "--component", "api", "--since", "yesterday"],
    ["tail", "--component", "api", "--regex", "("],
    ["tail", "--component", "api", "--regex", "a" * 201],
    ["tail", "--component", "api", "--level", "LOUD"],
    ["run", "--run-id", "x; rm -rf /"],
])
def test_bad_requests_are_refused(root, argv):
    with pytest.raises(reader.UsageError):
        run(root, *argv)


def test_lines_are_capped(root):
    items, _ = run(root, "tail", "--component", "api", "--lines", "100000")
    assert len(items) <= reader.MAX_LINES


def test_output_is_capped_and_says_so():
    stream = io.StringIO()
    reader.emit([{"msg": "x" * 1000}] * 200, {"matched": 200}, stream)
    lines = stream.getvalue().splitlines()
    meta = json.loads(lines[-1])["_meta"]
    assert len(stream.getvalue()) <= reader.MAX_OUTPUT + 200
    assert meta["truncated"] > 0 and meta["returned"] == len(lines) - 1


def test_the_ssh_command_line_is_parsed_without_a_shell(monkeypatch):
    seen = []
    monkeypatch.setattr(reader, "execute", lambda argv: seen.append(argv) or ([], {}))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND",
                       "factorlab-log-reader tail --component api --grep 'a b; ls'")
    assert reader.main() == 0
    assert seen == [["tail", "--component", "api", "--grep", "a b; ls"]]
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "tail --grep 'unterminated")
    assert reader.main() == 2


def test_redaction_patterns_match_the_writer():
    from factorlab.core import logging as core_logging

    assert reader.SECRET_KEY.pattern == core_logging._SECRET_KEY.pattern
    assert reader.SECRET_TEXT.pattern == core_logging._SECRET_TEXT.pattern
