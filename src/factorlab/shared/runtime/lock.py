"""File-based mutex for orchestrators.

Lifted verbatim from the political runner so every long-running script can
refuse to overlap with a sibling. The lock file records PID + start time;
if it's older than ``stale_seconds`` (default 12h), it's treated as a stale
remnant of a crashed run and stolen.

Raises ``SystemExit`` (with the configured exit code in the message) when the
lock is held by another fresh process. Callers should catch ``SystemExit`` and
return :data:`factorlab.shared.runtime.exit_codes.EXIT_LOCK_HELD`.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

LOCK_STALE_SECONDS_DEFAULT = 12 * 3600  # 12 hours


@contextmanager
def acquire_lock(
    path: Path,
    *,
    stale_seconds: int = LOCK_STALE_SECONDS_DEFAULT,
) -> Iterator[None]:
    """Refuse to start if another orchestrator is running at *path*.

    Implementation: lock file holds ``{"pid": ..., "started_at": <UTC ISO>}``.
    If the file exists AND its age < ``stale_seconds``, raise ``SystemExit``.
    If older (or malformed), treat as stale and steal. Always cleans up on exit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
            started_at = datetime.fromisoformat(content["started_at"])
            age = (datetime.now(timezone.utc) - started_at).total_seconds()
        except Exception:
            age = stale_seconds + 1  # malformed → treat as stale
        if age < stale_seconds:
            raise SystemExit(
                f"[lock] another orchestrator holds {path} (age={age:.0f}s, "
                f"stale_threshold={stale_seconds}s) -- refusing to overlap. "
                f"If you know the prior run is dead, delete the file."
            )
        log.warning("[lock] stealing stale lock (age=%.0fs): %s", age, path)
    try:
        path.write_text(
            json.dumps({
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }),
            encoding="utf-8",
        )
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["acquire_lock", "LOCK_STALE_SECONDS_DEFAULT"]
