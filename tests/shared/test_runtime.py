"""Tests for factorlab.shared.runtime — Phase 3 lift contracts."""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from factorlab.shared.runtime import (
    EXIT_CRASH,
    EXIT_FATAL,
    EXIT_LOCK_HELD,
    EXIT_NOT_TRADING_DAY,
    EXIT_OK,
    EXIT_WARN,
    ExitCode,
    GracefulShutdown,
    Heartbeat,
    MarketWindow,
    RunState,
    WatermarkTracker,
    acquire_lock,
    dated_log_dir,
    load_run_state,
    mark_fail,
    mark_ok,
    save_run_state,
    setup_logging,
    should_run,
    supervised,
)


# ── ExitCode ────────────────────────────────────────────────────────────────


def test_exit_code_int_values():
    assert ExitCode.OK == 0
    assert ExitCode.WARN == 2
    assert ExitCode.FATAL == 3
    assert ExitCode.NOT_TRADING_DAY == 10
    assert ExitCode.LOCK_HELD == 75
    assert ExitCode.CRASH == 99


def test_exit_code_backcompat_aliases():
    # Module-level ints must match the enum (used by _political_runner re-export).
    assert EXIT_OK == int(ExitCode.OK)
    assert EXIT_WARN == int(ExitCode.WARN)
    assert EXIT_FATAL == int(ExitCode.FATAL)
    assert EXIT_NOT_TRADING_DAY == int(ExitCode.NOT_TRADING_DAY)
    assert EXIT_LOCK_HELD == int(ExitCode.LOCK_HELD)
    assert EXIT_CRASH == int(ExitCode.CRASH)


# ── setup_logging / dated_log_dir ───────────────────────────────────────────


def test_setup_logging_writes_dated_file(tmp_path):
    logger = setup_logging("test_script", log_root=tmp_path, level=logging.INFO)
    logger.info("hello")
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    expected = tmp_path / f"test_script_{today}.log"
    # Flush handlers so the file is on disk
    for h in logging.getLogger().handlers:
        h.flush()
    assert expected.exists()
    assert "hello" in expected.read_text(encoding="utf-8")


def test_dated_log_dir_creates_directory(tmp_path):
    d = dated_log_dir("political_daily", base=tmp_path)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert d == tmp_path / f"political_daily_{today}"
    assert d.is_dir()


# ── GracefulShutdown ────────────────────────────────────────────────────────


def test_graceful_shutdown_flips_on_sigterm():
    sd = GracefulShutdown()
    sd.install()
    try:
        assert sd.triggered is False
        sd._handler(signal.SIGTERM, None)  # synthetic
        assert sd.triggered is True
    finally:
        sd.restore()


def test_graceful_shutdown_context_manager():
    before = signal.getsignal(signal.SIGINT)
    with GracefulShutdown() as sd:
        assert sd.triggered is False
        # While inside the with-block, the SIGINT handler is OURS, not the default.
        inside = signal.getsignal(signal.SIGINT)
        assert inside is not before
        # And the bound method points to *this* GracefulShutdown instance.
        assert getattr(inside, "__self__", None) is sd
    # Exiting the block restores the previous handler.
    after = signal.getsignal(signal.SIGINT)
    assert after is before


# ── WatermarkTracker ────────────────────────────────────────────────────────


def test_watermark_tracker_filters_and_marks():
    wm = WatermarkTracker()
    df = pd.DataFrame({"bar_time": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])})

    # First seen: no watermark, everything passes
    out = wm.filter(df, "AAPL")
    assert len(out) == 3
    wm.mark(out, "AAPL")
    assert "AAPL" in wm

    # Second sweep: watermark blocks already-seen rows
    out2 = wm.filter(df, "AAPL")
    assert len(out2) == 0

    # New bar arrives
    df2 = pd.DataFrame({"bar_time": pd.to_datetime(["2026-01-04"])})
    out3 = wm.filter(df2, "AAPL")
    assert len(out3) == 1
    wm.mark(out3, "AAPL")


def test_watermark_tracker_custom_time_col():
    wm = WatermarkTracker()
    df = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"])})
    out = wm.filter(df, "X", time_col="timestamp")
    assert len(out) == 2
    wm.mark(out, "X", time_col="timestamp")
    assert wm.get("X") == pd.Timestamp("2026-01-02")


def test_watermark_tracker_clear():
    wm = WatermarkTracker()
    df = pd.DataFrame({"bar_time": pd.to_datetime(["2026-01-01"])})
    wm.mark(df, "AAPL")
    assert len(wm) == 1
    wm.clear()
    assert len(wm) == 0


# ── MarketWindow ────────────────────────────────────────────────────────────


def test_market_window_open_close_dt():
    et = ZoneInfo("America/New_York")
    mw = MarketWindow(
        calendar_key="XNYS",
        open_time=dt_time(9, 30),
        close_time=dt_time(16, 0),
        tz=et,
        pre_open_min=30,
    )
    day = datetime(2026, 1, 5, tzinfo=et).date()  # a Monday
    assert mw.open_dt(day).hour == 9
    assert mw.open_dt(day).minute == 30
    assert mw.close_dt(day).hour == 16
    # poll_start = open - pre_open_min
    assert mw.poll_start(day) == mw.open_dt(day) - timedelta(minutes=30)


def test_market_window_is_trading_day_known_session():
    et = ZoneInfo("America/New_York")
    mw = MarketWindow(
        calendar_key="XNYS",
        open_time=dt_time(9, 30),
        close_time=dt_time(16, 0),
        tz=et,
    )
    # 2026-01-02 is a Friday — NYSE open. New Year's Day (Jan 1) closed.
    assert mw.is_trading_day(datetime(2026, 1, 2).date()) is True
    assert mw.is_trading_day(datetime(2026, 1, 3).date()) is False  # Saturday


# ── acquire_lock ────────────────────────────────────────────────────────────


def test_acquire_lock_creates_and_clears(tmp_path):
    lock = tmp_path / "test.lock"
    assert not lock.exists()
    with acquire_lock(lock):
        assert lock.exists()
        body = json.loads(lock.read_text(encoding="utf-8"))
        assert body["pid"] == os.getpid()
    assert not lock.exists()


def test_acquire_lock_refuses_overlap(tmp_path):
    lock = tmp_path / "busy.lock"
    # Plant a fresh lock as if another process is running
    lock.write_text(json.dumps({
        "pid": 99999,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))
    with pytest.raises(SystemExit) as exc_info:
        with acquire_lock(lock, stale_seconds=3600):
            pass
    assert "another orchestrator" in str(exc_info.value)


def test_acquire_lock_steals_stale(tmp_path):
    lock = tmp_path / "stale.lock"
    # Plant an obviously-stale lock (1 hour ago, threshold = 1s)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    lock.write_text(json.dumps({"pid": 99999, "started_at": old}))
    with acquire_lock(lock, stale_seconds=1):
        # Stole it: the file now contains OUR pid
        body = json.loads(lock.read_text(encoding="utf-8"))
        assert body["pid"] == os.getpid()


# ── RunState + should_run + mark_ok / mark_fail ─────────────────────────────


def test_run_state_round_trip(tmp_path):
    state_file = tmp_path / "run_state.json"
    state: dict[str, RunState] = {}
    mark_ok("source_a", state)
    mark_fail("source_b", state, retry_after_sec=3600, error="429 Too Many Requests")
    save_run_state(state_file, state)

    loaded = load_run_state(state_file)
    assert loaded["source_a"].last_ok is not None
    assert loaded["source_b"].last_fail is not None
    assert loaded["source_b"].retry_after_at is not None
    assert "429" in loaded["source_b"].last_error


def test_should_run_first_time():
    ok, reason = should_run("never_seen", {})
    assert ok is True
    assert "first run" in reason


def test_should_run_in_deferred_cooldown():
    state: dict[str, RunState] = {}
    mark_fail("fec", state, retry_after_sec=3600)
    ok, reason = should_run("fec", state)
    assert ok is False
    assert "deferred" in reason


def test_should_run_success_cooldown_blocks():
    state: dict[str, RunState] = {}
    mark_ok("legislators", state)
    ok, reason = should_run("legislators", state, success_cooldown_hours=6.0)
    assert ok is False
    assert "recently succeeded" in reason


def test_should_run_zero_cooldown_always_runs():
    state: dict[str, RunState] = {}
    mark_ok("legislators", state)
    ok, _reason = should_run("legislators", state, success_cooldown_hours=0.0)
    assert ok is True


def test_mark_ok_clears_deferred_cooldown():
    state: dict[str, RunState] = {}
    mark_fail("congress_gov", state, retry_after_sec=3600)
    assert state["congress_gov"].retry_after_at is not None
    mark_ok("congress_gov", state)
    assert state["congress_gov"].retry_after_at is None
    assert state["congress_gov"].consecutive_failures == 0


# ── Heartbeat ───────────────────────────────────────────────────────────────


def test_heartbeat_creates_file_and_advances_mtime():
    hb = Heartbeat("zz_test_runtime")
    assert hb.path.exists()
    first_mtime = hb.path.stat().st_mtime
    time.sleep(0.05)
    hb.tick()
    second_mtime = hb.path.stat().st_mtime
    assert second_mtime >= first_mtime


# ── supervised ──────────────────────────────────────────────────────────────


def test_supervised_returns_main_exit_code():
    rc = supervised(lambda: int(ExitCode.WARN), name="zz_test_supervised")
    assert rc == int(ExitCode.WARN)


def test_supervised_converts_uncaught_to_crash():
    def boom():
        raise RuntimeError("boom")
    rc = supervised(boom, name="zz_test_crash")
    assert rc == int(ExitCode.CRASH)


def test_supervised_passes_system_exit_through():
    def quit_(): raise SystemExit(7)
    with pytest.raises(SystemExit) as exc:
        supervised(quit_, name="zz_test_systemexit")
    assert exc.value.code == 7


def test_supervised_calls_on_crash_callback():
    captured: list[BaseException] = []
    def boom(): raise ValueError("xyz")
    rc = supervised(boom, name="zz_test_crashcb",
                    on_crash=lambda exc: captured.append(exc))
    assert rc == int(ExitCode.CRASH)
    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)
