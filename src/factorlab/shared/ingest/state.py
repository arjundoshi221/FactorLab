"""Per-source resume checkpoint — JSON-backed Set[str].

Lifted from political/_state. Every long backfill that wants to be re-entrant
holds a ``State`` instance. Every UPSERT is idempotent so the checkpoint is
an *optimization*, not a correctness requirement — losing it means more
replay work on resume, never duplicate rows.

Usage::

    state = State(source='house_clerk_ptr')
    if state.is_done(f"{year}/{doc_id}"):
        continue
    process(...)
    state.mark_done(f"{year}/{doc_id}")

The checkpoint file lives at ``state_dir(source)/<source>.json``.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from factorlab.shared.paths import state_dir

log = logging.getLogger(__name__)

# OneDrive briefly holds files open while indexing/syncing, which makes
# os.replace fail with WinError 5 (Access is denied). The window is usually
# <1s; retrying a few times is enough to clear it without giving up.
_REPLACE_RETRIES = 6
_REPLACE_BACKOFF_SEC = 0.5


class State:
    """Per-source checkpoint set, persisted as JSON.

    The ``namespace`` arg lets political-style sources keep their nested
    layout: ``State(source='house_clerk_ptr', namespace='political')`` writes
    to ``state_dir('political') / 'house_clerk_ptr.json'``. Default behaviour
    (no namespace) writes to ``state_dir(source) / 'state.json'``.
    """

    def __init__(self, source: str, *, namespace: str | None = None) -> None:
        self.source = source
        self.namespace = namespace
        if namespace is not None:
            self.path = state_dir(namespace) / f"{source}.json"
        else:
            self.path = state_dir(source) / "state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._done: set[str] = set(self._load())

    def _load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("[%s] state file unreadable, starting fresh: %s",
                        self.source, e)
            return []

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(sorted(self._done)), encoding="utf-8")
        last_exc: PermissionError | None = None
        for attempt in range(_REPLACE_RETRIES):
            try:
                tmp.replace(self.path)
                return
            except PermissionError as e:
                last_exc = e
                time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))
        log.warning(
            "[%s] state save failed after %d retries (OneDrive lock?): %s",
            self.source, _REPLACE_RETRIES, last_exc,
        )
        try:
            tmp.unlink()
        except OSError:
            pass

    def is_done(self, key: str) -> bool:
        return key in self._done

    def mark_done(self, key: str, flush: bool = False) -> None:
        with self._lock:
            self._done.add(key)
            if flush:
                self._save()

    def flush(self) -> None:
        with self._lock:
            self._save()

    def reset(self) -> None:
        """Clear all checkpoints for this source."""
        with self._lock:
            self._done.clear()
            self._save()

    def __len__(self) -> int:
        return len(self._done)

    def __contains__(self, key: str) -> bool:
        return key in self._done


__all__ = ["State"]
