"""Process-owned mutex for orchestrators with a JSON ownership record.

An OS lock on a persistent sibling .guard file prevents overlap and releases
on process exit. Never delete the guard file: contenders must lock the same
inode. Older JSON-only locks retain their stale timeout during migration.
"""

from __future__ import annotations

import errno
import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)
LOCK_STALE_SECONDS_DEFAULT = 12 * 3600


@contextmanager
def _guard(path: Path) -> Generator[None]:
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
        else:
            import fcntl
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise SystemExit(
                f"[lock] another orchestrator holds {path} -- refusing to overlap."
            ) from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def acquire_lock(
    path: Path,
    *,
    stale_seconds: int = LOCK_STALE_SECONDS_DEFAULT,
) -> Generator[None]:
    """Refuse overlapping collectors and recover guarded locks after a crash.

    stale_seconds applies only to records from the JSON-only implementation.
    Stop old collectors before upgrading; mixed implementations cannot share
    a lock safely during a rolling upgrade.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _guard(path.with_name(path.name + ".guard")):
        if path.exists():
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                guarded = content.get("lock_version") == 2
                started_at = datetime.fromisoformat(content["started_at"])
                age = (datetime.now(UTC) - started_at).total_seconds()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                guarded = False
                age = stale_seconds + 1
            if not guarded and age < stale_seconds:
                raise SystemExit(
                    f"[lock] another orchestrator holds {path} (age={age:.0f}s, "
                    f"stale_threshold={stale_seconds}s) -- refusing to overlap. "
                    "If you know the prior run is dead, delete the file."
                )
            log.warning("[lock] recovering abandoned lock (age=%.0fs): %s", age, path)
        path.write_text(
            json.dumps(
                {"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat(), "lock_version": 2}
            ),
            encoding="utf-8",
        )
        try:
            yield
        finally:
            path.unlink(missing_ok=True)


__all__ = ["LOCK_STALE_SECONDS_DEFAULT", "acquire_lock"]
