"""EODHD historical-bars Backfiller — first wired example of the protocol.

Each work-unit is one ``(symbol, from_date, to_date)`` triple. ``plan()``
just produces the unit list; ``run()`` iterates symbols sequentially and
returns a :class:`BackfillReport`.

The free EODHD tier caps daily API calls at ~20 / day, so the dispatcher is
deliberately sequential (no thread pool). Per-call disk caching is provided
by the underlying ``EODHDClient`` if the caller wires one in; this Backfiller
only handles the orchestration shape.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Callable, ClassVar

from factorlab.shared.ingest.backfill import (
    BackfillPlan,
    BackfillReport,
    register_backfiller,
)
from factorlab.countries.us.equities.eodhd.candles import fetch_daily_bars
from factorlab.countries.us.equities.eodhd.client import EODHDClient

log = logging.getLogger(__name__)


# Free-tier demo symbols (no API key required beyond ``demo``).
DEMO_SYMBOLS = ["AAPL.US", "TSLA.US", "AMZN.US", "VTI.US"]


class EODHDBackfiller:
    """Wraps the EOD-bars fetch loop in the Backfiller protocol."""

    source: ClassVar[str] = "eodhd"

    def __init__(self, *, symbols: list[str] | None = None,
                 api_key: str | None = None) -> None:
        self.symbols = symbols or DEMO_SYMBOLS
        self.api_key = api_key or os.getenv("EODHD_API_KEY", "demo")

    # ── plan ────────────────────────────────────────────────────────────

    def plan(
        self,
        *,
        since: date | None = None,
        until: date | None = None,
        **kwargs: Any,
    ) -> BackfillPlan:
        since_s = since.isoformat() if since else None
        until_s = until.isoformat() if until else None
        units = [
            {"symbol": s, "from_date": since_s, "to_date": until_s}
            for s in self.symbols
        ]
        return BackfillPlan(
            source=self.source,
            units=units,
            estimated_cost={
                "api_calls": len(units),
                "tier": "free (~20/day)" if self.api_key == "demo" else "paid",
            },
            notes=[
                f"symbols: {len(units)}",
                f"window: {since_s or '-'} -> {until_s or '-'}",
            ],
        )

    # ── run ─────────────────────────────────────────────────────────────

    def run(
        self,
        plan: BackfillPlan,
        *,
        dry_run: bool = False,
        notify_fn: Callable[..., Any] | None = None,
    ) -> BackfillReport:
        report = BackfillReport(source=self.source)
        if dry_run:
            report.notes.append(
                f"dry-run: would fetch {plan.unit_count} symbols"
            )
            return report

        client = EODHDClient(api_key=self.api_key)
        t0 = time.time()
        for u in plan.units:
            symbol = u["symbol"]
            try:
                df = fetch_daily_bars(
                    client, symbol,
                    from_date=u.get("from_date"),
                    to_date=u.get("to_date"),
                )
                if df is None or df.empty:
                    report.units_skipped += 1
                    report.notes.append(f"{symbol}: no data")
                    continue
                report.units_done += 1
                report.rows_written += len(df)
            except Exception as e:
                msg = f"{symbol}: {type(e).__name__}: {e}"
                log.error("[eodhd] %s", msg)
                report.units_failed += 1
                report.errors.append(msg)
                if notify_fn is not None:
                    try:
                        notify_fn(
                            subject=f"eodhd backfill error: {symbol}",
                            body=msg,
                            severity="warn",
                            source="eodhd_backfill",
                        )
                    except Exception:
                        pass
        report.duration_sec = time.time() - t0
        return report


# Auto-register on import.
register_backfiller(EODHDBackfiller.source, EODHDBackfiller())


__all__ = ["EODHDBackfiller", "DEMO_SYMBOLS"]
