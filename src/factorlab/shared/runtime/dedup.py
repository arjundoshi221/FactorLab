"""WatermarkTracker — per-key max-timestamp memoiser for live pollers.

The two live pollers (`us_equities_schwab_live` (TBD rename), `india_equities_upstox_live`) maintain
an in-memory ``{symbol: last_bar_time}`` dict so that successive sweeps don't
re-write bars they've already inserted. This module lifts that pattern.

Usage::

    wm = WatermarkTracker()
    for symbol in symbols:
        df = fetch(symbol)
        df = wm.filter(df, key=symbol)            # drop already-seen rows
        if df.empty:
            continue
        write_candles(df, ...)
        wm.mark(df, key=symbol)                   # advance watermark

    wm.clear()                                    # reset at new trading day

The default time column is ``bar_time`` (matches `write_candles`). Pass
``time_col=`` for sources that use a different name (Upstox uses ``timestamp``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


class WatermarkTracker:
    """In-memory ``{key: latest_seen_time}`` map for dedup."""

    def __init__(self) -> None:
        self._watermarks: dict[str, datetime] = {}

    def get(self, key: str) -> datetime | None:
        return self._watermarks.get(key)

    def filter(self, df: "pd.DataFrame", key: str, *, time_col: str = "bar_time") -> "pd.DataFrame":
        """Return only rows whose ``time_col`` is strictly newer than the watermark.

        If no watermark yet, returns the input unchanged.
        """
        wm = self._watermarks.get(key)
        if wm is None or df is None or df.empty:
            return df
        return df[df[time_col] > wm]

    def mark(self, df: "pd.DataFrame", key: str, *, time_col: str = "bar_time") -> None:
        """Advance the watermark for ``key`` to the max ``time_col`` in ``df``.

        Only advances forward — never moves the watermark backwards.
        """
        if df is None or df.empty:
            return
        try:
            max_t = df[time_col].max()
        except KeyError:
            return
        existing = self._watermarks.get(key)
        if existing is None or max_t > existing:
            self._watermarks[key] = max_t

    def clear(self) -> None:
        """Forget all watermarks. Typically called at the start of a new trading day."""
        self._watermarks.clear()

    def __len__(self) -> int:
        return len(self._watermarks)

    def __contains__(self, key: str) -> bool:
        return key in self._watermarks


__all__ = ["WatermarkTracker"]
