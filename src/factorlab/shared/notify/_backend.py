"""Backend protocol + shared types for the notify module."""

from __future__ import annotations

import html
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    FAIL = "fail"
    FATAL = "fatal"


# Severity → (label color, row accent color) for the HTML banner.
_SEV_PALETTE = {
    Severity.INFO:  ("#0b5cad", "#dbeafe"),
    Severity.WARN:  ("#8a5a00", "#fef3c7"),
    Severity.FAIL:  ("#a40e1f", "#fee2e2"),
    Severity.FATAL: ("#7a0011", "#fecaca"),
}


@dataclass(frozen=True)
class Notification:
    """One alert.

    Core: ``subject``, ``body``, ``severity``, ``source``.

    Structured metadata (all optional — fall through to "—" in the HTML
    table if not supplied; omitted entirely from the JSONL row when None):

    - ``script``    — entrypoint path, e.g. ``scripts/in/equities/upstox/india_equities_upstox_live.py``
    - ``frequency`` — cadence label, e.g. ``"daily 06:00 IST"``, ``"5-min session"``
    - ``vendor``    — data vendor, e.g. ``"Upstox"``, ``"Schwab"``, ``"EODHD"``
    - ``domain``    — ``"equities"`` | ``"political"`` | ``"fundamentals"`` | ...
    - ``country``   — ISO-ish two-letter, e.g. ``"IN"``, ``"US"``
    - ``context``   — free-form key-value dict rendered as a 2-col table
                      (row counts, watermark timestamps, retry counts, etc.)

    ``occurred_at`` is set automatically at construction (UTC).
    ``host`` is filled with the local machine name if not provided.
    """
    subject: str
    body: str
    severity: Severity
    source: str
    script: str | None = None
    frequency: str | None = None
    vendor: str | None = None
    domain: str | None = None
    country: str | None = None
    context: Mapping[str, Any] | None = None
    host: str | None = None
    occurred_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self):
        # Frozen dataclass — bypass via object.__setattr__
        if self.occurred_at is None:
            object.__setattr__(self, "occurred_at", datetime.now(timezone.utc))
        if self.host is None:
            object.__setattr__(
                self, "host",
                os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown",
            )

    # ── Rendering ───────────────────────────────────────────────────────

    def subject_with_tag(self) -> str:
        sev = self.severity.value.upper()
        bits = [f"[FactorLab {sev}]"]
        if self.country:
            bits.append(f"[{self.country.upper()}]")
        if self.vendor:
            bits.append(self.vendor)
        bits.append(self.subject)
        return " ".join(bits)

    def _meta_rows(self) -> list[tuple[str, str]]:
        """Ordered (label, value) pairs for the metadata table."""
        return [
            ("Severity",  self.severity.value.upper()),
            ("Source",    self.source),
            ("Script",    self.script or "—"),
            ("Frequency", self.frequency or "—"),
            ("Vendor",    self.vendor or "—"),
            ("Domain",    self.domain or "—"),
            ("Country",   self.country or "—"),
            ("Host",      self.host or "—"),
            ("Occurred",  self.occurred_at.isoformat()),
        ]

    def html_body(self) -> str:
        """Return an Outlook-friendly HTML rendering.

        Uses inline styles only — Outlook strips <style> blocks. Layout is a
        single 2-col table for the metadata block + a <pre> for the body +
        an optional context table.
        """
        label_color, accent = _SEV_PALETTE[self.severity]
        sev_upper = self.severity.value.upper()
        esc = html.escape

        # Banner
        banner = (
            f"<div style='background:{accent};border-left:4px solid {label_color};"
            f"padding:10px 14px;margin-bottom:14px;'>"
            f"<span style='color:{label_color};font-weight:700;font-size:14px;"
            f"font-family:Segoe UI,Arial,sans-serif;'>"
            f"[{esc(sev_upper)}] {esc(self.source)}"
            f"</span><br/>"
            f"<span style='color:#444;font-size:13px;font-family:Segoe UI,Arial,sans-serif;'>"
            f"{esc(self.subject)}"
            f"</span>"
            f"</div>"
        )

        # Metadata table
        meta_rows_html = "".join(
            f"<tr>"
            f"<td style='padding:4px 12px 4px 0;color:#57606a;width:110px;'>{esc(label)}</td>"
            f"<td style='padding:4px 0;color:#1f2328;font-family:Consolas,Menlo,monospace;'>"
            f"{esc(value)}</td>"
            f"</tr>"
            for label, value in self._meta_rows()
        )
        meta = (
            f"<table cellspacing='0' cellpadding='0' "
            f"style='font-size:12px;font-family:Segoe UI,Arial,sans-serif;"
            f"border-collapse:collapse;margin-bottom:14px;'>"
            f"{meta_rows_html}"
            f"</table>"
        )

        # Body block
        body_html = (
            f"<pre style='background:#f6f8fa;padding:12px;border:1px solid #d0d7de;"
            f"border-radius:6px;white-space:pre-wrap;font-size:12px;"
            f"font-family:Consolas,Menlo,monospace;line-height:1.45;margin:0 0 14px 0;'>"
            f"{esc(self.body)}</pre>"
        )

        # Optional context table
        context_html = ""
        if self.context:
            ctx_rows = "".join(
                f"<tr>"
                f"<td style='padding:3px 12px 3px 0;color:#57606a;'>{esc(str(k))}</td>"
                f"<td style='padding:3px 0;color:#1f2328;"
                f"font-family:Consolas,Menlo,monospace;'>{esc(str(v))}</td>"
                f"</tr>"
                for k, v in self.context.items()
            )
            context_html = (
                f"<div style='font-size:12px;color:#57606a;margin-bottom:4px;"
                f"font-family:Segoe UI,Arial,sans-serif;'>Context</div>"
                f"<table cellspacing='0' cellpadding='0' "
                f"style='font-size:12px;font-family:Segoe UI,Arial,sans-serif;"
                f"border-collapse:collapse;margin-bottom:14px;'>"
                f"{ctx_rows}"
                f"</table>"
            )

        footer = (
            f"<p style='color:#8c959f;font-size:10px;font-family:Segoe UI,Arial,sans-serif;"
            f"margin-top:18px;border-top:1px solid #d0d7de;padding-top:8px;'>"
            f"FactorLab automated alert · audit: logs/notify.jsonl"
            f"</p>"
        )

        return (
            f"<html><body style='margin:0;padding:18px;background:#ffffff;color:#1f2328;'>"
            f"{banner}{meta}{body_html}{context_html}{footer}"
            f"</body></html>"
        )

    def text_body(self) -> str:
        """Plain-text rendering for SMTP fallback / Telegram / logs."""
        lines = [f"[{self.severity.value.upper()}] {self.source}  ·  {self.subject}", ""]
        for label, value in self._meta_rows():
            lines.append(f"{label:<10}{value}")
        lines.extend(["", self.body])
        if self.context:
            lines.append("")
            lines.append("Context:")
            for k, v in self.context.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Structured dict for JSONL persistence. Omits None fields."""
        out: dict[str, Any] = {
            "ts": self.occurred_at.isoformat(),
            "severity": self.severity.value,
            "source": self.source,
            "subject": self.subject,
            "body": self.body,
        }
        for k in ("script", "frequency", "vendor", "domain", "country", "host"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.context:
            out["context"] = dict(self.context)
        return out


class Backend(Protocol):
    """A backend turns a Notification into a side effect (email, HTTP POST, …).

    ``send`` should return True on success and False otherwise — callers in
    :mod:`factorlab.shared.notify` already wrap exceptions, but backends are
    free to raise too.
    """

    def send(self, notification: Notification) -> bool:  # pragma: no cover
        ...


class DedupeRing:
    """In-memory (key → first-seen monotonic ts) for suppressing repeats.

    Garbage-collects on each query so memory stays bounded.
    """

    def __init__(self, window_seconds: float = 300.0):
        self._window = float(window_seconds)
        self._seen: dict[str, float] = {}

    def already_sent(self, key: str) -> bool:
        now = time.monotonic()
        # GC
        if self._seen:
            self._seen = {k: t for k, t in self._seen.items()
                          if now - t < self._window}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def reset(self) -> None:
        self._seen.clear()


__all__ = ["Severity", "Notification", "Backend", "DedupeRing"]
