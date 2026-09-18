"""Cross-cutting runtime primitives shared by every live script and orchestrator.

Submodules:
    exit_codes — canonical ExitCode enum + legacy module-level constants
    logging    — setup_logging + dated_log_dir
    signals    — GracefulShutdown context manager
    dedup      — WatermarkTracker (per-key max bar_time)
    market     — MarketWindow (calendar + open/close/pre-open helpers)
    lock       — acquire_lock (file-based mutex with stale detection)
    state      — RunState + per-source should_run / mark_ok / mark_fail
    heartbeat  — Heartbeat (mtime liveness probe)
    supervised — supervised(main, ...) wrapper for top-level entrypoints
"""

from factorlab.shared.runtime.exit_codes import (
    EXIT_CRASH,
    EXIT_FATAL,
    EXIT_LOCK_HELD,
    EXIT_NOT_TRADING_DAY,
    EXIT_OK,
    EXIT_WARN,
    ExitCode,
)
from factorlab.shared.runtime.dedup import WatermarkTracker
from factorlab.shared.runtime.health import HealthReporter
from factorlab.shared.runtime.heartbeat import Heartbeat
from factorlab.shared.runtime.lock import acquire_lock
from factorlab.shared.runtime.logging import dated_log_dir, setup_logging
from factorlab.shared.runtime.market import MarketWindow
from factorlab.shared.runtime.signals import GracefulShutdown
from factorlab.shared.runtime.state import (
    RunState,
    load_run_state,
    mark_fail,
    mark_ok,
    save_run_state,
    should_run,
)
from factorlab.shared.runtime.supervised import supervised

__all__ = [
    # exit codes
    "ExitCode",
    "EXIT_OK", "EXIT_WARN", "EXIT_FATAL",
    "EXIT_NOT_TRADING_DAY", "EXIT_LOCK_HELD", "EXIT_CRASH",
    # logging
    "setup_logging", "dated_log_dir",
    # signals
    "GracefulShutdown",
    # dedup
    "WatermarkTracker",
    # health
    "HealthReporter",
    # market
    "MarketWindow",
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
