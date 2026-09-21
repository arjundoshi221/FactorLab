"""Unified notification module.

One public entry point — :func:`notify` — fans alerts out to one or more
backends selected by the ``FACTORLAB_NOTIFY_BACKEND`` env var.

Backends:

================  =============================================  ============
key               description                                    env vars
================  =============================================  ============
``outlook``       win32com.client → Outlook MailItem             FACTORLAB_NOTIFY_TO
``smtp``          smtplib via Gmail / M365 / etc                 SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FACTORLAB_NOTIFY_TO
``telegram``      Bot API → chat ID                              TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
``webhook``       HTTP POST to local notifier daemon             FACTORLAB_NOTIFY_URL, FACTORLAB_NOTIFY_PIN
``jsonl``         append to LOG_ROOT/notify.jsonl                (none)
``null``          log-only (tests, dry-run)                      (none)
================  =============================================  ============

``FACTORLAB_NOTIFY_BACKEND`` accepts a comma-separated list (e.g.
``outlook,jsonl``). ``jsonl`` is **always** appended even if not in the list —
it's the canonical audit record.

The recipient address (``FACTORLAB_NOTIFY_TO``) is read from env at send
time and NEVER written to any log line. Per project rule, it must not appear
in code.

Dedupe: by default we suppress alerts that repeat ``(source, severity, subject)``
within a 5-minute window. Caller can override the dedupe key via
``dedupe_key=`` or disable with ``dedupe_key=False``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Mapping

from factorlab.shared.notify._backend import (
    Backend,
    DedupeRing,
    Notification,
    Severity,
)
from factorlab.shared.notify.jsonl import JSONLBackend
from factorlab.shared.notify.null import NullBackend

log = logging.getLogger(__name__)

# Module-level dedupe ring — 5-minute window.
_DEDUPE = DedupeRing(window_seconds=300.0)


# ── Backend resolution ──────────────────────────────────────────────────────


def _backend_names() -> list[str]:
    raw = os.getenv("FACTORLAB_NOTIFY_BACKEND", "outlook").strip().lower()
    names = [n.strip() for n in raw.split(",") if n.strip()]
    # JSONL is always-on as the canonical audit trail.
    if "jsonl" not in names:
        names.append("jsonl")
    return names


def _resolve_backend(name: str) -> Backend | None:
    if name == "null":
        return NullBackend()
    if name == "jsonl":
        return JSONLBackend()
    if name == "outlook":
        try:
            from factorlab.shared.notify.outlook import OutlookBackend
            return OutlookBackend()
        except ImportError as e:
            log.warning("[notify] outlook backend unavailable: %s", e)
            return None
    if name == "smtp":
        try:
            from factorlab.shared.notify.smtp import SMTPBackend
            return SMTPBackend()
        except Exception as e:
            log.warning("[notify] smtp backend unavailable: %s", e)
            return None
    if name == "telegram":
        try:
            from factorlab.shared.notify.telegram import TelegramBackend
            return TelegramBackend()
        except Exception as e:
            log.warning("[notify] telegram backend unavailable: %s", e)
            return None
    if name == "webhook":
        try:
            from factorlab.shared.notify.webhook import WebhookBackend
            return WebhookBackend()
        except Exception as e:
            log.warning("[notify] webhook backend unavailable: %s", e)
            return None
    log.warning("[notify] unknown backend %r — ignoring", name)
    return None


def _build_backends() -> list[Backend]:
    out: list[Backend] = []
    for name in _backend_names():
        b = _resolve_backend(name)
        if b is not None:
            out.append(b)
    return out


# ── Public API ──────────────────────────────────────────────────────────────


def notify(
    subject: str,
    body: str,
    *,
    severity: Severity | str = "warn",
    source: str = "unspecified",
    script: str | None = None,
    frequency: str | None = None,
    vendor: str | None = None,
    domain: str | None = None,
    country: str | None = None,
    context: Mapping[str, Any] | None = None,
    dedupe_key: str | None | bool = None,
    backends: Iterable[Backend] | None = None,
) -> list[bool]:
    """Send a notification through every configured backend.

    Returns a list of per-backend success bools (parallel to backend order).

    Parameters
    ----------
    subject, body : str
        Message text. ``subject`` becomes the email subject / toast title.
    severity : 'info' | 'warn' | 'fail' | 'fatal'
        Determines display urgency. ``fatal`` always bypasses dedupe.
    source : str
        Short identifier of who fired the alert (e.g. ``'india_equities_upstox_live'``).
    script, frequency, vendor, domain, country : str | None
        Structured metadata for the alert. Rendered as a table in the HTML
        email and persisted as columns in ``logs/notify.jsonl``. All optional.
    context : Mapping[str, Any] | None
        Free-form key-value extras (row counts, watermarks, retry attempts).
        Rendered as a second table in the HTML email.
    dedupe_key : str | None | False
        If None: synthesise ``(source, severity, subject)`` and dedupe within 5min.
        If False: never dedupe.
        If str: explicit key.
    backends : Iterable[Backend] | None
        Override env-driven backend selection (for tests).
    """
    sev = severity if isinstance(severity, Severity) else Severity(str(severity).lower())

    # Dedupe (fatal always escapes)
    if dedupe_key is not False and sev != Severity.FATAL:
        key = dedupe_key if isinstance(dedupe_key, str) else f"{source}|{sev}|{subject}"
        if _DEDUPE.already_sent(key):
            log.debug("[notify] suppressed duplicate within window: %s", key)
            return []

    notification = Notification(
        subject=subject,
        body=body,
        severity=sev,
        source=source,
        script=script,
        frequency=frequency,
        vendor=vendor,
        domain=domain,
        country=country,
        context=context,
    )

    backend_list = list(backends) if backends is not None else _build_backends()
    results: list[bool] = []
    for backend in backend_list:
        try:
            ok = bool(backend.send(notification))
        except Exception as e:
            log.warning("[notify] backend %s raised: %s",
                        backend.__class__.__name__, e)
            ok = False
        results.append(ok)
    return results


__all__ = ["notify", "Notification", "Severity"]
