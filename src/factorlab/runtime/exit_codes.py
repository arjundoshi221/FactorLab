"""Canonical exit codes for every FactorLab script.

The enum is the authoritative spelling. Module-level constants (``EXIT_OK``,
``EXIT_WARN``, ...) exist for back-compat with the original
``scripts/_political_runner.py`` consumers.

| Code | Meaning            | Used by                                                |
|------|--------------------|--------------------------------------------------------|
| 0    | OK                 | clean run                                              |
| 2    | WARN               | non-fatal source failure / warn-level anomaly          |
| 3    | FATAL              | DB / schema / fail-level anomaly                       |
| 10   | NOT_TRADING_DAY    | calendar said today isn't a session (not an error)     |
| 75   | LOCK_HELD          | another orchestrator instance is running (POSIX EX_TEMPFAIL) |
| 99   | CRASH              | uncaught exception                                     |
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    WARN = 2
    FATAL = 3
    NOT_TRADING_DAY = 10
    LOCK_HELD = 75
    CRASH = 99


# Module-level aliases for back-compat with _political_runner.py callers.
EXIT_OK: int = int(ExitCode.OK)
EXIT_WARN: int = int(ExitCode.WARN)
EXIT_FATAL: int = int(ExitCode.FATAL)
EXIT_NOT_TRADING_DAY: int = int(ExitCode.NOT_TRADING_DAY)
EXIT_LOCK_HELD: int = int(ExitCode.LOCK_HELD)
EXIT_CRASH: int = int(ExitCode.CRASH)


__all__ = [
    "ExitCode",
    "EXIT_OK",
    "EXIT_WARN",
    "EXIT_FATAL",
    "EXIT_NOT_TRADING_DAY",
    "EXIT_LOCK_HELD",
    "EXIT_CRASH",
]
