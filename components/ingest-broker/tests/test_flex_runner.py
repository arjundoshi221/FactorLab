import json
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer

import pytest

from factorlab.components.ingest_broker.flex_runner import (
    COMMAND,
    DailySchedule,
    FlexRunner,
    build_parser,
    make_handler,
    parse_slots,
    parse_summary,
)

T0 = datetime(2026, 10, 30, 12, tzinfo=UTC)
SUMMARY = {
    "run_id": "r-1",
    "source": "ibkr",
    "pipeline": "ibkr_flex_activity",
    "status": "success",
    "rows_written": 42,
    "failed_units": [],
}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def completed(stdout: str = "", stderr: str = "", code: int = 0):
    return lambda *a, **k: subprocess.CompletedProcess(COMMAND, code, stdout, stderr)


def runner(run, clock=None, spawned=None, **kwargs) -> FlexRunner:
    return FlexRunner(
        run=run,
        clock=clock or Clock(),
        spawn=(spawned.append if spawned is not None else lambda target: target()),
        **kwargs,
    )


def test_parse_summary_takes_the_engine_report_among_other_output():
    stdout = "noise {not json}\n" + json.dumps(SUMMARY, indent=1) + "\n"
    assert parse_summary(stdout) == SUMMARY
    assert parse_summary("no summary here") is None


def test_start_runs_once_and_refuses_overlap_then_cooldown():
    clock, spawned = Clock(), []
    r = runner(completed(json.dumps(SUMMARY, indent=1)), clock, spawned, cooldown_seconds=300)
    assert r.latest() == {"state": "idle"}

    status, job = r.start()
    assert status == 202 and job["state"] == "running"
    assert r.start()[0] == 409

    spawned.pop()()
    latest = r.latest()
    assert latest["state"] == "success"
    assert (latest["run_id"], latest["rows_written"], latest["error"]) == ("r-1", 42, None)

    clock.now = T0 + timedelta(seconds=100)
    status, body = r.start()
    assert status == 429 and body["retry_after"] == 201
    clock.now = T0 + timedelta(seconds=301)
    assert r.start()[0] == 202


def test_failed_units_and_partial_status_are_reported():
    summary = {
        **SUMMARY,
        "status": "partial",
        "failed_units": [{"name": "flex:x", "error": "boom"}],
    }
    r = runner(completed(json.dumps(summary)))
    r.start()
    latest = r.latest()
    assert latest["state"] == "partial" and latest["error"] == "flex:x: boom"

    r = runner(completed(json.dumps({**summary, "status": "failed"})))
    r.start()
    assert r.latest()["state"] == "failed"


def test_missing_summary_and_timeout_fail_with_a_reason():
    r = runner(completed(stderr="AuthRequired: IBKR Flex SendRequest failed (1013)", code=3))
    r.start()
    latest = r.latest()
    assert latest["state"] == "failed"
    assert "exit 3 with no run summary" in latest["error"] and "1013" in latest["error"]

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(COMMAND, 600)

    r = runner(timeout, timeout_seconds=600)
    r.start()
    assert r.latest()["error"] == "timed out after 600s"


def test_command_is_the_manual_engine_run():
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(SUMMARY), "")

    runner(run, timeout_seconds=12).start()
    assert seen["cmd"] == list(COMMAND) and seen["timeout"] == 12


@pytest.fixture
def server():
    spawned: list = []
    r = runner(completed(json.dumps(SUMMARY)), spawned=spawned)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(r))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", spawned
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(url: str, method: str = "GET") -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_endpoints(server):
    base, spawned = server
    assert call(f"{base}/healthz") == (200, {"ok": True})
    assert call(f"{base}/runs/latest") == (200, {"state": "idle"})
    assert call(f"{base}/runs", "POST")[0] == 202
    assert call(f"{base}/runs", "POST")[0] == 409
    spawned.pop()()
    assert call(f"{base}/runs/latest")[1]["state"] == "success"
    assert call(f"{base}/nope")[0] == 404
    assert call(f"{base}/runs/latest", "POST")[0] == 404


def test_daily_slots_parse_and_reject_bad_input():
    assert [slot.strftime("%H:%M") for slot in parse_slots(" 18:00,06:30,06:30,")] == [
        "06:30",
        "18:00",
    ]
    assert parse_slots("") == []
    for bad in ("6", "25:00", "06:xx"):
        with pytest.raises(Exception, match="invalid slot"):
            parse_slots(bad)


def test_daily_schedule_fires_each_new_york_slot_once_per_day():
    # 05:00 New York (EDT, UTC-4) on 30 Oct 2026: the 06:30 slot is still ahead.
    start = datetime(2026, 10, 30, 9, tzinfo=UTC)
    schedule = DailySchedule(parse_slots("06:30"), start)
    assert schedule.due(start + timedelta(hours=1)) == []  # 06:00
    assert [
        slot.strftime("%H:%M") for slot in schedule.due(start + timedelta(hours=1, minutes=31))
    ] == ["06:30"]
    assert schedule.due(start + timedelta(hours=3)) == []  # already fired today
    assert len(schedule.due(start + timedelta(days=1, hours=2))) == 1  # next day


def test_daily_schedule_skips_slots_already_past_at_start():
    late = datetime(2026, 10, 30, 20, tzinfo=UTC)  # 16:00 New York
    schedule = DailySchedule(parse_slots("06:30"), late)
    assert schedule.due(late + timedelta(minutes=5)) == []  # a restart never fires a missed slot


def test_daily_at_is_off_unless_given():
    assert build_parser().parse_args([]).daily_at == []
    assert build_parser().parse_args(["--daily-at", ""]).daily_at == []  # compose's "" disables
    parsed = build_parser().parse_args(["--daily-at", "07:15"]).daily_at
    assert [slot.strftime("%H:%M") for slot in parsed] == ["07:15"]
