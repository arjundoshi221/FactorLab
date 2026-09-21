"""JSONLBackend — append every notification to LOG_ROOT/notify.jsonl.

This backend is **always-on** alongside whatever primary backend is configured
— it's the canonical audit record. If Outlook / SMTP / Telegram fails or the
daemon is down, the JSONL still captures the alert.

Schema (one JSON object per line)::

    {"ts": "<UTC ISO>", "severity": "warn", "source": "...",
     "subject": "...", "body": "..."}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from factorlab.shared.notify._backend import Backend, Notification
from factorlab.shared.paths import log_dir

log = logging.getLogger(__name__)


class JSONLBackend(Backend):
    """Append notification as a JSON line to ``LOG_ROOT/notify.jsonl``."""

    def __init__(self, path: Path | None = None):
        self.path = path or (log_dir() / "notify.jsonl")

    def send(self, notification: Notification) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(notification.to_dict(), default=str) + "\n")
            return True
        except OSError as e:
            log.error("[notify:jsonl] write to %s failed: %s", self.path, e)
            return False


__all__ = ["JSONLBackend"]
