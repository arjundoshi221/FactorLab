"""Heartbeat — mtime-based liveness probe for long-running scripts.

Each long-running script holds one ``Heartbeat`` and calls ``.tick()`` on
each loop iteration. Docker healthchecks inspect the file's mtime. When configured,
ticks also send rate-limited UDP heartbeats to the service monitor.

Heartbeat files live under ``FACTORLAB_HEARTBEAT_ROOT`` (default
``<repo>/data/_heartbeat``). One file per service.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from factorlab.core.paths import heartbeat_path
from factorlab.runtime.monitor import monitor_client


class Heartbeat:
    """One service's liveness file. Calling ``tick()`` is O(1)."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.path: Path = heartbeat_path(service)
        self.monitor = monitor_client(service)
        self._next_send = 0.0
        # touch on construction so a brand-new service has a mtime immediately
        self.tick()

    def tick(self) -> None:
        """Update mtime. Safe to call from many threads (best-effort)."""
        now = time.monotonic()
        if now >= self._next_send:
            self._next_send = now + self.monitor.interval_s
            self.monitor.heartbeat()
        try:
            self.path.touch(exist_ok=True)
            # On Windows, ``touch`` updates mtime only if the file exists; for
            # belt-and-suspenders, also call os.utime which is symmetric.
            os.utime(self.path, None)
        except OSError:
            # Heartbeats are advisory. A failed touch must never crash the
            # caller — the supervisor will eventually notice via mtime.
            pass

    def __repr__(self) -> str:  # pragma: no cover
        return f"Heartbeat(service={self.service!r}, path={self.path})"


__all__ = ["Heartbeat"]
