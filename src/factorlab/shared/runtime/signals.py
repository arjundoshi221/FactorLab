"""Graceful shutdown helper for long-running pollers.

Use as a context manager — installs SIGINT/SIGTERM handlers on enter, restores
previous handlers on exit. Loops check ``.triggered`` each iteration::

    with GracefulShutdown(log) as shutdown:
        while not shutdown.triggered:
            poll_once(...)
            time.sleep(60)

If you need to pass the flag to a worker function, pass the ``GracefulShutdown``
instance — workers check ``shutdown.triggered`` the same way.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType

log = logging.getLogger(__name__)


class GracefulShutdown:
    """SIGINT/SIGTERM → flip ``triggered`` flag. Idempotent across re-entry."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._triggered = False
        self._prev: dict[int, object] = {}
        self._log = logger or log
        self._installed = False

    @property
    def triggered(self) -> bool:
        return self._triggered

    def _handler(self, signum: int, _frame: FrameType | None) -> None:
        if not self._triggered:
            self._log.info(
                "Received signal %s -- shutting down after current iteration",
                signal.Signals(signum).name,
            )
        self._triggered = True

    def install(self) -> None:
        """Install handlers (idempotent)."""
        if self._installed:
            return
        self._prev[signal.SIGINT] = signal.signal(signal.SIGINT, self._handler)
        self._prev[signal.SIGTERM] = signal.signal(signal.SIGTERM, self._handler)
        self._installed = True

    def restore(self) -> None:
        """Restore previously-installed handlers."""
        if not self._installed:
            return
        for sig, prev in self._prev.items():
            try:
                signal.signal(sig, prev)
            except (TypeError, ValueError):
                # Some signal handlers can't be set back (e.g. the default
                # value on certain platforms). Best-effort.
                pass
        self._installed = False

    def __enter__(self) -> "GracefulShutdown":
        self.install()
        return self

    def __exit__(self, *exc_info) -> None:
        self.restore()


__all__ = ["GracefulShutdown"]
