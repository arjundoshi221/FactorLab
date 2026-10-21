"""Point-in-time lookups over effective-dated rows (membership, sectors, shares).

History in FactorLab starts at the first snapshot. Backfilled index history before
that date uses the earliest known state (``clamp_before_first``): survivorship bias,
documented in ADR 0017, in exchange for a usable long history.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Span[T]:
    """``value`` holds for ``listing`` from ``start`` through ``end`` (inclusive; None = open)."""

    listing: UUID
    value: T
    start: date
    end: date | None = None

    def covers(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day <= self.end)


class Timeline[T]:
    """``on(day)`` -> {listing: value} from spans; cached between change points."""

    def __init__(self, spans: Iterable[Span[T]], *, clamp_before_first: bool = True) -> None:
        self.spans = sorted(spans, key=lambda s: (s.start, str(s.listing)))
        self.first = self.spans[0].start if self.spans else None
        self.clamp = clamp_before_first
        bounds = {s.start for s in self.spans}
        bounds |= {date.fromordinal(s.end.toordinal() + 1) for s in self.spans if s.end}
        self._changes = sorted(bounds)
        self._cache: tuple[int, dict[UUID, T]] | None = None

    def on(self, day: date) -> dict[UUID, T]:
        if self.first is None:
            return {}
        if self.clamp and day < self.first:
            day = self.first
        epoch = bisect_right(self._changes, day)
        if self._cache is None or self._cache[0] != epoch:
            self._cache = (epoch, {s.listing: s.value for s in self.spans if s.covers(day)})
        return self._cache[1]


class Observations:
    """Latest observation at or before a day per listing (e.g. shares outstanding)."""

    def __init__(
        self, rows: Mapping[UUID, Iterable[tuple[date, float]]], *, clamp_before_first: bool = True
    ) -> None:
        self._dates: dict[UUID, list[date]] = {}
        self._values: dict[UUID, list[float]] = {}
        for listing, observed in rows.items():
            ordered = sorted(observed)
            self._dates[listing] = [d for d, _ in ordered]
            self._values[listing] = [v for _, v in ordered]
        self.clamp = clamp_before_first

    def on(self, listing: UUID, day: date) -> float | None:
        dates = self._dates.get(listing)
        if not dates:
            return None
        i = bisect_right(dates, day)
        if i == 0:
            return self._values[listing][0] if self.clamp else None
        return self._values[listing][i - 1]


__all__ = ["Observations", "Span", "Timeline"]
