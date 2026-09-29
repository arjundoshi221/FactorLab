"""Snapshot -> membership diff rules shared by every universe sink."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from factorlab.ingest.universe_snapshot import SnapshotRejected, diff_snapshot

A, B, C, D, E, F = (uuid.uuid4() for _ in range(6))
DAY = date(2026, 9, 24)


def test_first_snapshot_opens_everything():
    diff = diff_snapshot("sp500", {}, {A, B}, as_of=DAY)
    assert set(diff.add) == {A, B} and diff.close == () and diff.effective_from == DAY


def test_changes_open_new_members_and_close_leavers_the_day_before():
    since = date(2026, 1, 2)
    current = {A: since, B: since, C: since, D: since, E: since, F: since}
    diff = diff_snapshot("sp500", current, {A, B, C, D, E, uuid.UUID(int=7)}, as_of=DAY)
    assert diff.add == (uuid.UUID(int=7),)
    assert diff.close == ((F, since),) and diff.effective_to == date(2026, 9, 23)


def test_unchanged_snapshot_is_a_no_op():
    assert diff_snapshot("sp500", {A: DAY}, {A}, as_of=DAY).unchanged


def test_implausible_snapshots_are_refused():
    current = dict.fromkeys((A, B, C, D, E), DAY)
    with pytest.raises(SnapshotRejected):
        diff_snapshot("sp500", current, {A, B}, as_of=DAY)  # drops 3 of 5
    with pytest.raises(SnapshotRejected):
        diff_snapshot("sp500", current, set(), as_of=DAY)
