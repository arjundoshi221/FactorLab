"""Snapshot -> effective-dated membership diff, shared by every universe sink.

Pure logic so the in-memory and ClickHouse sinks cannot drift apart:
given the currently open memberships and a new complete snapshot, return what
to open and what to close, or refuse a snapshot that looks broken.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

MAX_REMOVAL_FRACTION = 0.2


class SnapshotRejected(ValueError):
    """The snapshot would change membership implausibly; nothing is written."""


@dataclass(frozen=True, slots=True)
class MembershipDiff:
    add: tuple[UUID, ...]
    close: tuple[tuple[UUID, date], ...]  # (listing_id, effective_from of the open row)
    effective_from: date
    effective_to: date

    @property
    def unchanged(self) -> bool:
        return not self.add and not self.close


def diff_snapshot(universe_code: str, current: Mapping[UUID, date], snapshot: set[UUID], *,
                  as_of: date,
                  max_removal_fraction: float = MAX_REMOVAL_FRACTION) -> MembershipDiff:
    """``current`` maps open listing -> its ``effective_from``; ``snapshot`` is the new membership."""
    if not snapshot:
        raise SnapshotRejected(f"{universe_code}: snapshot resolved to no listings")
    removed = [listing for listing in current if listing not in snapshot]
    if current and len(removed) > max_removal_fraction * len(current):
        raise SnapshotRejected(
            f"{universe_code}: snapshot drops {len(removed)} of {len(current)} members "
            f"(> {max_removal_fraction:.0%}); refusing as implausible")
    return MembershipDiff(
        add=tuple(sorted((listing for listing in snapshot if listing not in current), key=str)),
        close=tuple(sorted(((listing, current[listing]) for listing in removed), key=str)),
        effective_from=as_of, effective_to=as_of - timedelta(days=1),
    )


__all__ = ["MAX_REMOVAL_FRACTION", "MembershipDiff", "SnapshotRejected", "diff_snapshot"]
