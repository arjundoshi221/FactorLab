"""SMTPBackend — generic SMTP sender (lifted from india_equities_upstox_premarket).

Required env vars: SMTP_USER, SMTP_PASSWORD, FACTORLAB_NOTIFY_TO.
Optional:           SMTP_HOST (default smtp.gmail.com), SMTP_PORT (default 587).

Sends both plain and HTML alternative parts.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from factorlab.shared.notify._backend import Backend, Notification

log = logging.getLogger(__name__)


class SMTPBackend(Backend):
    """Send via SMTP STARTTLS."""

    def send(self, notification: Notification) -> bool:
        to_addr = os.environ.get("FACTORLAB_NOTIFY_TO", "").strip()
        smtp_user = os.environ.get("SMTP_USER", "").strip()
        smtp_pass = os.environ.get("SMTP_PASSWORD", "").strip()
        if not (to_addr and smtp_user and smtp_pass):
            log.warning(
                "[notify:smtp] missing env (need SMTP_USER, SMTP_PASSWORD, "
                "FACTORLAB_NOTIFY_TO) -- skipping"
            )
            return False

        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
        smtp_port = int(os.environ.get("SMTP_PORT", "587").strip())

        msg = MIMEMultipart("alternative")
        msg["Subject"] = notification.subject_with_tag()
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg.attach(MIMEText(notification.body, "plain", "utf-8"))
        msg.attach(MIMEText(notification.html_body(), "html", "utf-8"))

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            return True
        except Exception as e:
            log.error("[notify:smtp] send failed: %s", e)
            return False


__all__ = ["SMTPBackend"]
