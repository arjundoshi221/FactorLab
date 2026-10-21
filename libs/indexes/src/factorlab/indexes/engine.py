"""Equal- and cap-weighted index construction, one trading day at a time (ADR 0017).

Pure: callers feed each day's closes in date order and receive that day's levels and
start-of-day weights. Every index advances in one pass, so memory is proportional to
the constituent count, not the history length.

For each index on day ``t`` after day ``p``:

* ``w`` are the start-of-day weights (they sum to 1), ``r_i = close_t / close_p - 1``
  (0 for a constituent without a close today, which then counts against coverage);
* ``R = sum(w_i * r_i)``, ``level_t = level_p * (1 + R)``, ``contribution_i = w_i * r_i``;
* at the close weights drift to ``w_i (1 + r_i) / (1 + R)``, then:
  - **cw**: reset to ``close_t * shares_i / sum`` over today's priced members;
  - **ew**: reset to ``1 / N`` on the first session on or after the third Friday of
    Mar/Jun/Sep/Dec; between rebalances, members that left are dropped and the rest
    renormalised (additions wait for the next rebalance).

``coverage`` is the start-of-day weight that had a close; a day below the gate is
still computed but marked ``complete = False``.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

Weighting = Literal["ew", "cw"]
METHODOLOGY_VERSION = "1"
_QUARTER_MONTHS = (3, 6, 9, 12)


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    code: str
    name: str
    weighting: Weighting
    sector_id: str | None = None  # None: every universe member
    base_value: float = 1000.0
    coverage_gate: float = 0.98


@dataclass(frozen=True, slots=True)
class ConstituentDay:
    listing: UUID
    weight: float  # start-of-day
    ret: float | None  # None: no close today
    contribution: float


@dataclass(frozen=True, slots=True)
class IndexDay:
    code: str
    trade_date: date
    level: float
    ret: float | None
    n_constituents: int
    coverage: float
    complete: bool
    constituents: tuple[ConstituentDay, ...] = ()


@dataclass(slots=True)
class _State:
    level: float
    weights: dict[UUID, float] = field(default_factory=dict)
    last_rebalance: date | None = None


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (calendar.FRIDAY - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def rebalance_due(last: date | None, today: date) -> bool:
    """True on the first session on or after a quarterly third Friday since ``last``."""
    if last is None:
        return True
    for year in (today.year - 1, today.year):
        for month in _QUARTER_MONTHS:
            target = third_friday(year, month)
            if last < target <= today:
                return True
    return False


def _normalise(weights: Mapping[UUID, float]) -> dict[UUID, float]:
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total > 0 else {}


class IndexEngine:
    """Advance ``definitions`` day by day from ``base_date``.

    ``members(day)`` maps each universe listing to its sector id (or None when
    unclassified); ``shares(listing, day)`` returns shares outstanding or None.
    """

    def __init__(
        self,
        definitions: Sequence[IndexDefinition],
        *,
        base_date: date,
        members: Callable[[date], Mapping[UUID, str | None]],
        shares: Callable[[UUID, date], float | None],
    ) -> None:
        if len({d.code for d in definitions}) != len(definitions):
            raise ValueError("index codes must be unique")
        self.definitions = tuple(definitions)
        self.base_date = base_date
        self.members = members
        self.shares = shares
        self.last_close: dict[UUID, float] = {}
        self.last_day: date | None = None
        self._states: dict[str, _State] = {}
        self._groups: tuple[date, dict[str | None, list[UUID]]] | None = None

    def _group(self, day: date, sector_id: str | None) -> list[UUID]:
        """Members on ``day`` in ``sector_id`` (None: all), grouped once per day."""
        if self._groups is None or self._groups[0] != day:
            groups: dict[str | None, list[UUID]] = {None: []}
            for lid, sector in self.members(day).items():
                groups[None].append(lid)
                if sector is not None:
                    groups.setdefault(sector, []).append(lid)
            self._groups = (day, groups)
        return self._groups[1].get(sector_id, [])

    def _eligible(self, definition: IndexDefinition, day: date) -> list[UUID]:
        return [lid for lid in self._group(day, definition.sector_id) if lid in self.last_close]

    def _target(self, definition: IndexDefinition, day: date) -> dict[UUID, float]:
        eligible = self._eligible(definition, day)
        if definition.weighting == "ew":
            return dict.fromkeys(eligible, 1.0)
        caps: dict[UUID, float] = {}
        for lid in eligible:
            count = self.shares(lid, day)
            if count:
                caps[lid] = self.last_close[lid] * count
        return caps

    def step(self, day: date, closes: Mapping[UUID, float]) -> list[IndexDay]:
        if self.last_day is not None and day <= self.last_day:
            raise ValueError(f"days must increase: {day} after {self.last_day}")
        if day < self.base_date:
            self.last_close.update(closes)
            self.last_day = day
            return []
        prior = dict(self.last_close)
        self.last_close.update(closes)
        out: list[IndexDay] = []
        for definition in self.definitions:
            state = self._states.get(definition.code)
            if state is None:
                out.append(self._start(definition, day))
                continue
            out.append(self._advance(definition, state, day, closes, prior))
        self.last_day = day
        return out

    def _start(self, definition: IndexDefinition, day: date) -> IndexDay:
        weights = _normalise(self._target(definition, day))
        self._states[definition.code] = _State(definition.base_value, weights, day)
        return IndexDay(
            definition.code, day, definition.base_value, None, len(weights), 1.0, bool(weights)
        )

    def _advance(
        self,
        definition: IndexDefinition,
        state: _State,
        day: date,
        closes: Mapping[UUID, float],
        prior: Mapping[UUID, float],
    ) -> IndexDay:
        rows: list[ConstituentDay] = []
        index_ret = coverage = 0.0
        returns: dict[UUID, float] = {}
        for lid, weight in state.weights.items():
            close, before = closes.get(lid), prior.get(lid)
            ret = close / before - 1.0 if close is not None and before else None
            returns[lid] = ret or 0.0
            if ret is not None:
                coverage += weight
            index_ret += weight * returns[lid]
            rows.append(ConstituentDay(lid, weight, ret, weight * returns[lid]))
        n = len(state.weights)
        if n:
            state.level *= 1.0 + index_ret
        drifted = _normalise({lid: w * (1.0 + returns[lid]) for lid, w in state.weights.items()})
        if definition.weighting == "cw":
            state.weights = _normalise(self._target(definition, day))
        elif rebalance_due(state.last_rebalance, day) or not drifted:
            state.weights = _normalise(self._target(definition, day))
            state.last_rebalance = day
        else:
            current = set(self._eligible(definition, day))
            state.weights = _normalise({k: v for k, v in drifted.items() if k in current})
        return IndexDay(
            definition.code,
            day,
            state.level,
            index_ret if n else None,
            n,
            coverage if n else 0.0,
            bool(n) and coverage >= definition.coverage_gate,
            tuple(rows),
        )


__all__ = [
    "METHODOLOGY_VERSION",
    "ConstituentDay",
    "IndexDay",
    "IndexDefinition",
    "IndexEngine",
    "rebalance_due",
    "third_friday",
]
