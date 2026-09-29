"""tools/read_logs.py: the client for the restricted factorlab-logs account."""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("read_logs_tool", REPO / "tools" / "read_logs.py")
client = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client
spec.loader.exec_module(client)


@pytest.fixture
def logs(tmp_path):
    root = tmp_path / "log"
    (root / "ingest-us").mkdir(parents=True)
    now = datetime.now(UTC)
    lines = [
        {"ts": (now - timedelta(minutes=30)).isoformat(), "level": "INFO",
         "component": "ingest-us", "service": "ingest-us", "logger": "factorlab.us",
         "msg": "run started", "run_id": "r-1", "pipeline": "us_live"},
        {"ts": (now - timedelta(minutes=20)).isoformat(), "level": "ERROR",
         "component": "ingest-us", "service": "ingest-us", "logger": "factorlab.us",
         "msg": "schwab 429 after 4 tries", "run_id": "r-1", "authorization": "Bearer x",
         "exc": "Traceback (most recent call last):\nHTTPError: 429 Too Many Requests"},
    ]
    (root / "ingest-us" / "ingest-us.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return root


def run(capsys, *argv):
    code = client.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_tail_prints_readable_lines_with_context_and_exception_summaries(logs, capsys):
    code, out, _ = run(capsys, "--local", str(logs), "tail", "--component", "ingest-us")
    assert code == 0
    first, second, summary, meta = out.splitlines()
    assert "INFO ingest-us/ingest-us factorlab.us: run started" in first
    assert "run_id=r-1" in first and "pipeline=us_live" in first
    assert "ERROR" in second and "authorization=[redacted]" in second
    assert summary.strip() == "-> HTTPError: 429 Too Many Requests"
    assert meta.startswith("-- ") and "matched=2" in meta


def test_errors_and_run_views(logs, capsys):
    _, out, _ = run(capsys, "--local", str(logs), "errors")
    assert "1x ingest-us/ingest-us factorlab.us: schwab <n> after <n> tries" in out
    _, out, _ = run(capsys, "--local", str(logs), "--json", "run", "--run-id", "r-1")
    records = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert [r["msg"] for r in records] == ["run started", "schwab 429 after 4 tries"]


def test_list_view(logs, capsys):
    _, out, _ = run(capsys, "--local", str(logs), "list")
    assert out.startswith("ingest-us: ingest-us.jsonl (")


def test_the_host_grammar_is_checked_before_connecting(capsys, monkeypatch):
    monkeypatch.setattr(client, "fetch_remote", lambda argv: pytest.fail("connected"))
    code, _, err = run(capsys, "tail")  # --component is required
    assert code == 2 and "--component" in err
    code, _, _ = run(capsys, "delete", "--everything")
    assert code == 2


def test_local_usage_errors_are_reported(logs, capsys):
    code, _, err = run(capsys, "--local", str(logs), "tail", "--component", "missing")
    assert code == 1 and "no logs for component" in err


def test_the_ssh_command_keeps_host_key_checking_and_quotes_arguments():
    command = client.ssh_command({"host": "vps", "port": "22", "user": "factorlab-logs",
                                  "key": "/k"}, ["tail", "--component", "api", "--grep", "a b;c"])
    assert "StrictHostKeyChecking=yes" in command and "BatchMode=yes" in command
    assert "ClearAllForwardings=yes" in command and "RequestTTY=no" in command
    assert command[-2] == "factorlab-logs@vps"
    assert shlex.split(command[-1]) == ["tail", "--component", "api", "--grep", "a b;c"]


def test_remote_errors_are_short_and_redacted(monkeypatch):
    monkeypatch.setattr(client, "settings", lambda: {"host": "h", "port": "22",
                                                     "user": "u", "key": ""})

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, 2, b"", b"error: invalid --run-id token=abc")

    monkeypatch.setattr(client.subprocess, "run", fake)
    with pytest.raises(client.ClientError, match=r"token=\[redacted\]"):
        client.fetch_remote(["run", "--run-id", "x"])


def test_output_is_capped_with_a_hint():
    text = "\n".join("x" * 100 for _ in range(500))
    capped = client.cap(text, 2048)
    assert len(capped.encode()) < 2300 and "output truncated" in capped


def test_values_are_redacted_after_parsing_even_with_escaped_quotes():
    payload = json.dumps({"msg": 'token=abc\\"x" and more', "password": "p"}) + "\n"
    out = client.render("tail", payload, as_json=True)
    record = json.loads(out.splitlines()[0])
    assert record["password"] == "[redacted]" and "abc" not in record["msg"]
