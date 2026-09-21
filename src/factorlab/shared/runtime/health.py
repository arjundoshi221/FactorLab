"""HealthReporter — periodic "session healthy" summary alerts.

Long-running pollers (India 5-min, US live) call this once per loop tick
so the operator gets a positive heartbeat email at a configurable cadence
(default hourly), not just silence-on-no-failure.

Pattern::

    reporter = HealthReporter(
        source="india_equities_upstox_live",
        script="scripts/in/equities/upstox/india_equities_upstox_live.py",
        frequency="5-min session (03:40-10:05 UTC)",
        vendor="Upstox",
        domain="equities",
        country="IN",
        interval_seconds=3600,   # 0 disables
    )

    while not shutdown.triggered:
        fetched, new = poll_once(...)
        reporter.record_sweep(
            fetched=fetched, new_bars=new, total_symbols=len(symbols),
        )
        reporter.maybe_report()       # fires only when interval has elapsed

    reporter.force_report(reason="market close")
    reporter.reset_day()              # daemon's new-day transition

All reports route through :func:`factorlab.shared.notify.notify` at
``severity='info'`` with full structured metadata (script/frequency/vendor
/domain/country) so they render with the standard HTML banner + metadata
table + context counters.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class HealthReporter:
    source: str
    script: str
    frequency: str
    vendor: str
    domain: str
    country: str
    interval_seconds: int = 3600  # 0 disables hourly reports entirely

    # Session counters (cumulative since last reset_day).
    _session_started: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    _sweeps: int = 0
    _symbols_polled_cum: int = 0
    _new_bars_cum: int = 0
    _non_fetched_cum: int = 0
    _last_sweep_at: datetime | None = None
    _last_sweep_fetched: int = 0
    _last_sweep_new: int = 0
    _last_sweep_total: int = 0
    _last_report_monotonic: float = field(default_factory=time.monotonic)

    def record_sweep(self, *, fetched: int, new_bars: int, total_symbols: int) -> None:
        self._sweeps += 1
        self._symbols_polled_cum += fetched
        self._new_bars_cum += new_bars
        self._non_fetched_cum += max(total_symbols - fetched, 0)
        self._last_sweep_at = datetime.now(timezone.utc)
        self._last_sweep_fetched = fetched
        self._last_sweep_new = new_bars
        self._last_sweep_total = total_symbols

    def maybe_report(self) -> bool:
        """Emit a health report if interval has elapsed. Returns True if sent."""
        if self.interval_seconds <= 0:
            return False
        if (time.monotonic() - self._last_report_monotonic) < self.interval_seconds:
            return False
        self._send_summary(
            subject=f"{self.source} health — session nominal",
            body=(
                f"Periodic health report from {self.source}.\n"
                f"{self._sweeps} poll sweeps since "
                f"{self._session_started.isoformat(timespec='seconds')}.\n"
                f"See Context table for full counters."
            ),
            severity="info",
        )
        self._last_report_monotonic = time.monotonic()
        return True

    def force_report(self, *, reason: str = "session complete") -> None:
        """Emit a summary regardless of interval (end-of-session)."""
        self._send_summary(
            subject=f"{self.source} session complete — {reason}",
            body=(
                f"End-of-session summary from {self.source} ({reason}).\n"
                f"Total sweeps: {self._sweeps}. "
                f"Total new bars: {self._new_bars_cum}.\n"
                f"See Context table for full counters."
            ),
            severity="info",
        )
        self._last_report_monotonic = time.monotonic()

    def reset_day(self) -> None:
        """Clear cumulative counters for the next trading day."""
        self._session_started = datetime.now(timezone.utc)
        self._sweeps = 0
        self._symbols_polled_cum = 0
        self._new_bars_cum = 0
        self._non_fetched_cum = 0
        self._last_sweep_at = None
        self._last_sweep_fetched = 0
        self._last_sweep_new = 0
        self._last_sweep_total = 0
        self._last_report_monotonic = time.monotonic()

    # ── Internals ──────────────────────────────────────────────────────

    def _summary_context(self) -> dict:
        polled_or_skipped = self._symbols_polled_cum + self._non_fetched_cum
        fetch_rate = (
            f"{(self._symbols_polled_cum / polled_or_skipped * 100):.1f}%"
            if polled_or_skipped > 0 else "n/a"
        )
        return {
            "session_started_utc":     self._session_started.isoformat(timespec="seconds"),
            "sweeps":                  self._sweeps,
            "symbols_polled_total":    self._symbols_polled_cum,
            "non_fetched_total":       self._non_fetched_cum,
            "fetch_success_rate":      fetch_rate,
            "new_bars_total":          self._new_bars_cum,
            "last_sweep_at_utc":       (self._last_sweep_at.isoformat(timespec="seconds")
                                        if self._last_sweep_at else "—"),
            "last_sweep_fetched":      f"{self._last_sweep_fetched}/{self._last_sweep_total}",
            "last_sweep_new_bars":     self._last_sweep_new,
            "report_interval_seconds": self.interval_seconds,
        }

    def _send_summary(self, *, subject: str, body: str, severity: str) -> None:
        # Local import to avoid module-load cycles.
        from factorlab.shared.notify import notify
        try:
            notify(
                subject=subject,
                body=body,
                severity=severity,
                source=self.source,
                script=self.script,
                frequency=self.frequency,
                vendor=self.vendor,
                domain=self.domain,
                country=self.country,
                context=self._summary_context(),
                dedupe_key=False,  # each interval tick is its own report
            )
        except Exception as e:  # never let an email failure crash the poller
            log.warning("[health] notify failed: %s", e)


__all__ = ["HealthReporter"]
