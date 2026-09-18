"""NullBackend — log-only; for tests and dry-run."""

from __future__ import annotations

import logging

from factorlab.shared.notify._backend import Backend, Notification

log = logging.getLogger(__name__)


class NullBackend(Backend):
    """Logs the notification at INFO level and returns True. No external I/O."""

    def send(self, notification: Notification) -> bool:
        log.info(
            "[notify:null] [%s] %s -- %s -- %s",
            notification.severity.value,
            notification.source,
            notification.subject,
            notification.body[:200],
        )
        return True


__all__ = ["NullBackend"]
