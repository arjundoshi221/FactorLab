"""Cross-cutting runtime primitives shared by every live script and orchestrator.

Submodules:
    exit_codes — canonical ExitCode enum + legacy module-level constants
    signals    — GracefulShutdown context manager
    lock       — acquire_lock (file-based mutex with stale detection)
    heartbeat  — Heartbeat (mtime liveness probe)
    supervised — supervised(main, ...) wrapper for top-level entrypoints
"""

from factorlab.runtime.exit_codes import (
    EXIT_CRASH,
    EXIT_FATAL,
    EXIT_LOCK_HELD,
    EXIT_NOT_TRADING_DAY,
    EXIT_OK,
    EXIT_WARN,
    ExitCode,
)
from factorlab.runtime.heartbeat import Heartbeat
from factorlab.runtime.lock import acquire_lock
from factorlab.runtime.signals import GracefulShutdown
from factorlab.runtime.supervised import supervised

__all__ = [
    "EXIT_CRASH",
    "EXIT_FATAL",
    "EXIT_LOCK_HELD",
    "EXIT_NOT_TRADING_DAY",
    "EXIT_OK",
    "EXIT_WARN",
    # exit codes
    "ExitCode",
    # logging
    # signals
    "GracefulShutdown",
    # heartbeat
    "Heartbeat",
    # lock
    "acquire_lock",
    # supervised
    "supervised",
]
