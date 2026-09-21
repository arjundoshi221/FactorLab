"""Backfiller protocol + registry + dispatcher-side dataclasses.

The contract: every source ships a class implementing :class:`Backfiller`
(or providing an equivalent ``.plan()`` + ``.run()`` signature). The
class registers itself via :func:`register_backfiller`, then
``scripts/_shared/factlab_backfill.py --source <name>`` looks it up and
invokes it.

``plan(...)`` is a cheap, side-effect-free preview — returns a
:class:`BackfillPlan` summarising what would be fetched (work units +
estimated cost). The dispatcher prints this when ``--dry-run`` is set.

``run(plan, dry_run, state, notify_fn)`` actually performs the work.
Returns a :class:`BackfillReport` with success/failure counts + per-unit
errors + free-form notes.

Per-source quirks (House Clerk's 4-tier classifier, FEC's key-rotating
pool, Schwab's per-symbol thread pool, Senate eFD's Playwright launch)
stay inside each source's Backfiller — they are NOT part of the protocol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, ClassVar, Protocol

log = logging.getLogger(__name__)


@dataclass
class BackfillPlan:
    """Cheap preview of a backfill — what would run, how big, why."""
    source: str
    units: list[dict[str, Any]] = field(default_factory=list)
    estimated_cost: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def unit_count(self) -> int:
        return len(self.units)


@dataclass
class BackfillReport:
    """Outcome of a backfill run."""
    source: str
    units_done: int = 0
    units_skipped: int = 0
    units_failed: int = 0
    rows_written: int = 0
    duration_sec: float = 0.0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.units_failed == 0


class Backfiller(Protocol):
    """Per-source backfill driver.

    Implementations are typically a small class that holds a SQLAlchemy
    engine + per-source clients. They register themselves at import time
    via :func:`register_backfiller`.
    """

    source: ClassVar[str]

    def plan(
        self,
        *,
        since: date | None = None,
        until: date | None = None,
        **kwargs: Any,
    ) -> BackfillPlan:
        """Cheap preview. Do not write to DB or hit external APIs heavily."""
        ...

    def run(
        self,
        plan: BackfillPlan,
        *,
        dry_run: bool = False,
        notify_fn: Callable[..., Any] | None = None,
    ) -> BackfillReport:
        """Execute the plan. ``dry_run=True`` skips DB writes."""
        ...


# ── Registry ────────────────────────────────────────────────────────────────


class BackfillRegistry:
    """Process-wide ``{source: Backfiller}`` map."""

    def __init__(self) -> None:
        self._items: dict[str, Backfiller] = {}

    def register(self, source: str, backfiller: Backfiller) -> None:
        if source in self._items:
            log.warning("[backfill] re-registering source=%r (was %s)",
                        source, type(self._items[source]).__name__)
        self._items[source] = backfiller

    def get(self, source: str) -> Backfiller | None:
        return self._items.get(source)

    def list(self) -> list[str]:
        return sorted(self._items.keys())


# Module-level singleton. Imports of source-specific modules register
# themselves here.
REGISTRY = BackfillRegistry()


def register_backfiller(source: str, backfiller: Backfiller) -> None:
    """Register a Backfiller under ``source``. Idempotent on re-import."""
    REGISTRY.register(source, backfiller)


def get_backfiller(source: str) -> Backfiller | None:
    """Look up the registered Backfiller for ``source``."""
    return REGISTRY.get(source)


def list_backfillers() -> list[str]:
    """Return the list of registered source codes."""
    return REGISTRY.list()


__all__ = [
    "BackfillPlan",
    "BackfillReport",
    "Backfiller",
    "BackfillRegistry",
    "REGISTRY",
    "register_backfiller",
    "get_backfiller",
    "list_backfillers",
]
