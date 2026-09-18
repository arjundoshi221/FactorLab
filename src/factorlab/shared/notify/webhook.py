"""WebhookBackend — POST a notification to the host-side notifier daemon.

The daemon (``python -m factorlab.shared.notify.daemon``) listens on
``127.0.0.1:8765`` and owns the Outlook COM handle. Container clients (when
we get there) hit it via ``http://host.docker.internal:8765/alert``.

Required env vars:
  FACTORLAB_NOTIFY_URL  (default: http://127.0.0.1:8765/alert)
  FACTORLAB_NOTIFY_PIN  (shared secret with daemon — required)
"""

from __future__ import annotations

import logging
import os

import requests

from factorlab.shared.notify._backend import Backend, Notification

log = logging.getLogger(__name__)


class WebhookBackend(Backend):
    """POST notification JSON to the daemon."""

    DEFAULT_URL = "http://127.0.0.1:8765/alert"

    def send(self, notification: Notification) -> bool:
        url = os.environ.get("FACTORLAB_NOTIFY_URL", self.DEFAULT_URL).strip()
        pin = os.environ.get("FACTORLAB_NOTIFY_PIN", "").strip()
        if not pin:
            log.warning(
                "[notify:webhook] FACTORLAB_NOTIFY_PIN not set -- daemon will "
                "reject the request"
            )
        headers = {"Content-Type": "application/json"}
        if pin:
            headers["X-Notify-Pin"] = pin
        payload = {
            "subject": notification.subject,
            "body": notification.body,
            "severity": notification.severity.value,
            "source": notification.source,
            "occurred_at": notification.occurred_at.isoformat(),
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error("[notify:webhook] POST %s failed: %s", url, e)
            return False


__all__ = ["WebhookBackend"]
