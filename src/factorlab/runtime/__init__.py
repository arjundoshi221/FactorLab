"""Cross-cutting runtime primitives shared by every live script and orchestrator.

Submodules:
    exit_codes — canonical ExitCode enum + legacy module-level constants
    signals    — GracefulShutdown context manager
    dedup      — WatermarkTracker (per-key max bar_time)
    lock       — acquire_lock (file-based mutex with stale detection)
    state      — RunState + per-source should_run / mark_ok / mark_fail
    heartbeat  — Heartbeat (mtime liveness probe)
    supervised — supervised(main, ...) wrapper for top-level entrypoints
"""

from factorlab.runtime.dedup import WatermarkTracker
from factorlab.runtime.exit_codes import (
    EXIT_CRASH,
    EXIT_FATAL,
    EXIT_LOCK_HELD,
    EXIT_NOT_TRADING_DAY,
    EXIT_OK,
    EXIT_WARN,
    ExitCode,
)
from factorlab.runtime.health import HealthReporter
from factorlab.runtime.heartbeat import Heartbeat
from factorlab.runtime.lock import acquire_lock
from factorlab.runtime.signals import GracefulShutdown
from factorlab.runtime.state import (
    RunState,
    load_run_state,
    mark_fail,
    mark_ok,
    save_run_state,
    should_run,
)
from factorlab.runtime.supervised import supervised

__all__ = [
    # exit codes
    "ExitCode",
    "EXIT_OK", "EXIT_WARN", "EXIT_FATAL",
    "EXIT_NOT_TRADING_DAY", "EXIT_LOCK_HELD", "EXIT_CRASH",
    # logging
    # signals
    "GracefulShutdown",
    # dedup
    "WatermarkTracker",
    # health
    "HealthReporter",
    # lock
    "acquire_lock",
    # state
    "RunState", "load_run_state", "save_run_state",
    "should_run", "mark_ok", "mark_fail",
    # heartbeat
    "Heartbeat",
    # supervised
    "supervised",
]
