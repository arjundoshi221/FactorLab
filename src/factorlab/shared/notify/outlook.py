"""OutlookBackend — drive the locally-installed Outlook via COM.

Default backend on this device. Uses pywin32's ``win32com.client`` to create
an Outlook ``MailItem`` and send it through the user's default profile. No
SMTP creds to manage; no third-party service.

Only available on Windows with Outlook installed AND a logged-in user
session. Inside a Linux container, the WebhookBackend is the correct path
— it POSTs to a host-side daemon that owns the COM handle.

Recipient address comes from ``FACTORLAB_NOTIFY_TO`` env var. NEVER hardcoded.
"""

from __future__ import annotations

import logging
import os

from factorlab.shared.notify._backend import Backend, Notification

log = logging.getLogger(__name__)


class OutlookBackend(Backend):
    """Send via win32com → Outlook MailItem."""

    OL_MAIL_ITEM = 0
    OL_FORMAT_HTML = 2

    def __init__(self):
        # Defer pywin32 import to construction so non-Windows callers fail
        # gracefully via factorlab.shared.notify._resolve_backend.
        import win32com.client  # type: ignore[import-not-found]
        self._win32com = win32com.client
        self._app = None  # lazily resolved per send

    def _outlook(self):
        """Get or create the Outlook Application COM object."""
        if self._app is None:
            self._app = self._win32com.Dispatch("Outlook.Application")
        return self._app

    def send(self, notification: Notification) -> bool:
        to = os.environ.get("FACTORLAB_NOTIFY_TO", "").strip()
        if not to:
            log.warning(
                "[notify:outlook] FACTORLAB_NOTIFY_TO not set -- cannot send"
            )
            return False
        try:
            outlook = self._outlook()
            mail = outlook.CreateItem(self.OL_MAIL_ITEM)
            mail.To = to
            mail.Subject = notification.subject_with_tag()
            mail.BodyFormat = self.OL_FORMAT_HTML
            mail.HTMLBody = notification.html_body()
            # FATAL: also flag for high importance
            if notification.severity.value == "fatal":
                mail.Importance = 2  # olImportanceHigh
            mail.Send()
            return True
        except Exception as e:
            log.error("[notify:outlook] send failed: %s", e)
            return False


__all__ = ["OutlookBackend"]
