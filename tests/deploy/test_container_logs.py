"""deploy/scripts/collect-container-logs.py: third-party logs reach the log reader, bounded."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from factorlab.core import logging as core_logging

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "collect_container_logs", REPO / "deploy/scripts/collect-container-logs.py"
)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)
reader_spec = importlib.util.spec_from_file_location(
    "factorlab_log_reader_for_collector", REPO / "deploy/host/factorlab_log_reader.py"
)
assert reader_spec and reader_spec.loader
reader = importlib.util.module_from_spec(reader_spec)
reader_spec.loader.exec_module(reader)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
NOW_NS = int(NOW.timestamp()) * 10**9


def stamp(seconds_ago: float, nanos: str = "") -> str:
    whole = datetime.fromtimestamp(NOW.timestamp() - seconds_ago, UTC)
    return whole.strftime("%Y-%m-%dT%H:%M:%S") + (f".{nanos}" if nanos else "") + "Z"


def done(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class Docker:
    """Answers docker ps / docker logs / journalctl from canned output."""

    def __init__(self, logs: dict[str, tuple[str, str]], journal: str = "", cursor_ok=True):
        self.logs, self.journal, self.cursor_ok, self.calls = logs, journal, cursor_ok, []

    def __call__(self, args: list[str]):
        self.calls.append(args)
        if args[:2] == ["docker", "ps"]:
            return done(
                "clickhouse\tfactorlab-clickhouse\napi\tfactorlab-api-1\n"
                "ibkr-gateway-paper\tfactorlab-ibkr-gateway-paper-1\n"
            )
        if args[:2] == ["docker", "logs"]:
            return done(*self.logs.get(args[-1], ("", "")))
        if args[0] == "journalctl":
            if "--after-cursor" in args and not self.cursor_ok:
                return done(code=1)
            return done(self.journal)
        raise AssertionError(args)


def read(root: Path, directory: str, service: str) -> list[dict]:
    path = root / directory / f"{service}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_redaction_patterns_match_the_writer():
    assert collector.SECRET_TEXT.pattern == core_logging._SECRET_TEXT.pattern
    assert collector.BOT_TOKEN.pattern == core_logging._BOT_TOKEN.pattern


def test_docker_timestamps_keep_nanoseconds():
    assert collector.docker_ns("2026-10-23T12:00:00.5Z") == NOW_NS + 500_000_000
    assert collector.docker_ns("2026-10-23T12:00:00.000000001Z") == NOW_NS + 1
    assert collector.docker_ns("2026-10-23T12:00:00Z") == NOW_NS


def test_levels_from_clickhouse_and_capitalised_words():
    assert collector.level_of("2026.10.23 12:00:00 [ 1 ] {} <Error> Application: x") == "ERROR"
    assert collector.level_of("... <Trace> MergeTree: y") == "DEBUG"
    assert collector.level_of("2026-10-23 12:00:00:123 IBC: WARN login slow") == "WARNING"
    assert collector.level_of("2026-10-23T12:00:00Z ERR failed to connect") == "ERROR"
    assert collector.level_of("0 errors so far") == "INFO"


def test_first_run_copies_the_lookback_window_drops_trace_and_redacts(tmp_path):
    docker = Docker(
        {
            "factorlab-clickhouse": (
                (
                    f"{stamp(60, '1')} <Information> Application: Ready for connections.\n"
                    f"{stamp(59)} <Trace> MergeTree: noisy\n"
                ),
                f"{stamp(30, '25')} <Error> TCPHandler: password=hunter2 rejected\n",
            ),
            "factorlab-ibkr-gateway-paper-1": (f"{stamp(10)} \x1b[32mIBC: login ok\x1b[0m\n", ""),
        }
    )
    state, failures = collector.collect(tmp_path, {}, NOW_NS, docker, None)

    assert failures == []
    logs = docker.calls[1]
    assert logs[:3] == ["docker", "logs", "--timestamps"]
    assert (
        logs[logs.index("--since") + 1] == f"{(NOW_NS - collector.LOOKBACK_NS) // 10**9}.000000000"
    )
    clickhouse = read(tmp_path, "clickhouse", "clickhouse")
    assert [r["level"] for r in clickhouse] == ["INFO", "ERROR"]
    assert clickhouse[1]["stream"] == "stderr"
    assert "hunter2" not in clickhouse[1]["msg"] and "password=[redacted]" in clickhouse[1]["msg"]
    assert clickhouse[0]["ts"] == "2026-10-23T11:59:00.100Z"
    assert read(tmp_path, "ibkr-gateway", "paper")[0]["msg"].endswith("IBC: login ok")
    # The next run starts after the newest line written, not at the window's edge.
    assert state["docker:clickhouse"] == NOW_NS - 30 * 10**9 + 250_000_000
    assert "docker:api" not in state  # only the listed third-party services are copied


def test_a_second_run_never_repeats_a_line(tmp_path):
    line = f"{stamp(30)} <Warning> Application: once\n"
    docker = Docker({"factorlab-clickhouse": (line, "")})
    state, _ = collector.collect(tmp_path, {}, NOW_NS, docker, None)
    collector.collect(tmp_path, state, NOW_NS + 60 * 10**9, docker, None)
    assert len(read(tmp_path, "clickhouse", "clickhouse")) == 1


def test_a_quiet_container_moves_its_cursor_to_the_settled_edge(tmp_path):
    state, _ = collector.collect(tmp_path, {}, NOW_NS, Docker({}), None)
    assert state["docker:clickhouse"] == NOW_NS - collector.SETTLE_NS
    assert not (tmp_path / "clickhouse").exists()


def test_a_log_storm_is_capped_with_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "MAX_RECORDS", 3)
    lines = "".join(f"{stamp(50 - i)} <Error> x {i}\n" for i in range(10))
    collector.collect(tmp_path, {}, NOW_NS, Docker({"factorlab-clickhouse": (lines, "")}), None)
    records = read(tmp_path, "clickhouse", "clickhouse")
    assert records[0]["level"] == "WARNING" and "dropped 7" in records[0]["msg"]
    assert [r["msg"] for r in records[1:]] == ["<Error> x 7", "<Error> x 8", "<Error> x 9"]


def test_journal_units_follow_their_cursor_and_recover_from_a_vacuumed_one(tmp_path):
    entry = {
        "__REALTIME_TIMESTAMP": str((NOW_NS - 5 * 10**9) // 1000),
        "__CURSOR": "s=abc;i=2",
        "PRIORITY": "6",
        "MESSAGE": "2026-10-23T11:59:55Z ERR Unable to reach the origin service",
    }
    docker = Docker({}, journal=json.dumps(entry) + "\n", cursor_ok=False)
    state, failures = collector.collect(
        tmp_path, {"journal:factorlab-cloudflared.service": "s=old"}, NOW_NS, docker, None
    )
    assert failures == []
    journal_calls = [c for c in docker.calls if c[0] == "journalctl"]
    assert "--after-cursor" in journal_calls[0] and "--since" in journal_calls[1]
    assert state["journal:factorlab-cloudflared.service"] == "s=abc;i=2"
    [record] = read(tmp_path, "edge", "cloudflared")
    assert record["level"] == "ERROR" and record["logger"] == "journal"


def test_one_failing_source_does_not_stop_the_others(tmp_path):
    docker = Docker({"factorlab-ibkr-gateway-paper-1": (f"{stamp(5)} INFO ok\n", "")})
    original = docker.__call__

    def runner(args):
        if args[-1] == "factorlab-clickhouse":
            return done(code=1)
        return original(args)

    state, failures = collector.collect(tmp_path, {}, NOW_NS, runner, None)
    assert failures == ["clickhouse: docker logs factorlab-clickhouse exited 1"]
    assert "docker:clickhouse" not in state
    assert read(tmp_path, "ibkr-gateway", "paper")[0]["msg"] == "INFO ok"


def test_the_log_reader_serves_collected_records(tmp_path):
    lines = f"{stamp(120)} <Error> Application: disk full\n"
    collector.collect(tmp_path, {}, NOW_NS, Docker({"factorlab-clickhouse": (lines, "")}), None)
    items, meta = reader.execute(
        ["tail", "--component", "clickhouse", "--level", "ERROR"], root=tmp_path, now=NOW
    )
    assert meta["matched"] == 1 and items[0]["msg"].endswith("disk full")
    groups, _ = reader.execute(["errors"], root=tmp_path, now=NOW)
    assert groups[0]["component"] == "clickhouse"


def test_state_round_trips(tmp_path):
    path = tmp_path / "state" / "state.json"
    collector.save_state(path, {"docker:clickhouse": 5})
    assert collector.load_state(path) == {"docker:clickhouse": 5}
    path.write_text("not json", encoding="utf-8")
    assert collector.load_state(path) == {}
