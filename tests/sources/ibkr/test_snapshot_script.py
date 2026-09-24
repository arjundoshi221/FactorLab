"""Entry point: argument parsing, daemon schedule loop, status reporting, exit codes."""

from __future__ import annotations

import argparse
import importlib.util
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from factorlab.shared.ingest.provider import RunSummary, UnitOutcome

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "us" / "ibkr" / "us_portfolio_ibkr_snapshot.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("us_portfolio_ibkr_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StatusStorage:
    def __init__(self):
        self.statuses = []

    def source_status(self, status, detail, *, source):
        self.statuses.append((source, status, detail))


def _summary(status, failed=()):
    units = [UnitOutcome("paper:positions", 3)] + [UnitOutcome(n, 0, "boom") for n in failed]
    return RunSummary(run_id=uuid.uuid4(), source="ibkr", pipeline="p", status=status,
                      rows_written=3, units=tuple(units))


def test_parse_modes_and_times(script):
    assert script.parse_modes("live, paper,live") == ("live", "paper")
    assert script.parse_times("16:30,06:00") == (time(6), time(16, 30))
    with pytest.raises(argparse.ArgumentTypeError):
        script.parse_modes("demo")
    with pytest.raises(argparse.ArgumentTypeError):
        script.parse_times("6pm")


def test_report_maps_run_status_and_notifies_on_partial(script, monkeypatch):
    sent = []
    monkeypatch.setattr(script, "_notify", lambda subject, body, **kw: sent.append((subject, kw)))
    storage = StatusStorage()
    script.report(storage, _summary("partial", failed=["live:connect"]), dry_run=False)
    assert storage.statuses[0][:2] == ("ibkr", "incomplete")
    assert "live:connect" in storage.statuses[0][2]
    assert sent[0][0] == "IBKR snapshot partial"
    assert sent[0][1]["severity"] == "warn"

    storage = StatusStorage()
    script.report(storage, _summary("success"), dry_run=False)
    assert storage.statuses[0][1] == "ready"


def test_report_dry_run_writes_nothing(script, monkeypatch):
    monkeypatch.setattr(script, "_notify", lambda *a, **k: pytest.fail("notified in dry run"))
    storage = StatusStorage()
    script.report(storage, _summary("failed", failed=["paper:connect"]), dry_run=True)
    assert storage.statuses == []


def test_daemon_runs_on_start_then_at_each_slot_and_survives_crashes(script, monkeypatch):
    monkeypatch.setattr(script, "_notify", lambda *a, **k: None)
    now = [datetime(2026, 9, 24, 12, 0, tzinfo=UTC)]  # Thu 08:00 New York
    runs = []

    def snapshot():
        runs.append(now[0])
        if len(runs) == 2:
            raise RuntimeError("clickhouse down")

    def sleep(seconds):
        now[0] += timedelta(seconds=seconds)

    storage = StatusStorage()
    code = script.run_daemon(
        storage, None, (time(6), time(16, 30)),
        should_stop=lambda: len(runs) >= 3, run_on_start=True,
        clock=lambda: now[0], sleep=sleep, snapshot=snapshot,
    )
    assert code == 0
    assert runs[0] == datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    assert runs[1] == datetime(2026, 9, 24, 20, 30, tzinfo=UTC)  # 16:30 EDT
    assert runs[2] == datetime(2026, 9, 25, 10, 0, tzinfo=UTC)  # next day 06:00 EDT
    assert [s[1] for s in storage.statuses].count("waiting") == 2
    assert storage.statuses[-1][1] == "stopped"


@pytest.mark.parametrize("status,code", [("success", 0), ("partial", 2), ("failed", 3)])
def test_once_exit_codes(script, monkeypatch, tmp_path, status, code):
    monkeypatch.setenv("IBKR_SNAPSHOT_LOCK", str(tmp_path / "lock"))
    monkeypatch.setenv("FACTORLAB_SECRETS_DIR", str(tmp_path))
    monkeypatch.setattr(script, "run_once", lambda storage, config, dry_run: _summary(status))
    assert script.main(["--once", "--dry-run", "--modes", "paper"]) == code
