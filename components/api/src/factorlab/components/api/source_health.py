"""Schedule-aware source and broker-snapshot freshness shared by every monitoring view.

A fixed age rule is wrong for the IBKR snapshot: it runs only at configured New York
slots on XNYS sessions (``IBKR_SNAPSHOT_TIMES``, the same variable ``ibkr-snapshot``
reads), so it is quiet overnight, at weekends and on holidays by design.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from pydantic_settings import SettingsConfigDict

from factorlab.calendars.us import previous_scheduled_run
from factorlab.core.settings import FactorLabSettings

DEFAULT_IBKR_SLOTS = "06:00,16:30"
SOURCE_MAX_AGE_SECONDS = 600
# A slot counts as missed only after this grace; a run takes a few minutes.
SLOT_GRACE = timedelta(hours=1)
# Snapshot clocks and slot times may differ slightly.
SLOT_TOLERANCE = timedelta(minutes=5)
SEVERE_SOURCE_STATUSES = frozenset({"error", "failed", "auth_required", "incomplete"})
# Normal between scheduled runs: the daemon reports when it will run next.
IDLE_SOURCE_STATUSES = {"ibkr": frozenset({"waiting"})}
SLOT_SOURCES = frozenset({"ibkr"})


class SnapshotScheduleSettings(FactorLabSettings):
    """``IBKR_SNAPSHOT_TIMES``: the same slots ``ibkr-snapshot`` runs at."""

    model_config = SettingsConfigDict(env_prefix="IBKR_SNAPSHOT_")

    times: str = DEFAULT_IBKR_SLOTS


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def ibkr_slots() -> tuple[time, ...]:
    """Parse ``IBKR_SNAPSHOT_TIMES``; invalid tokens are ignored, never fatal."""
    slots: set[time] = set()
    for raw in (SnapshotScheduleSettings().times, DEFAULT_IBKR_SLOTS):
        for token in (part.strip() for part in raw.split(",")):
            try:
                slots.add(time.fromisoformat(token))
            except ValueError:
                continue
        if slots:
            break
    return tuple(sorted(slots))


def previous_slot(now: datetime) -> datetime | None:
    try:
        return previous_scheduled_run(now, ibkr_slots())
    except RuntimeError:
        return None


def broker_snapshot_stale(stamp: datetime | None, now: datetime) -> bool:
    """Stale when a slot that is at least ``SLOT_GRACE`` old has no snapshot at or after it."""
    stamp = aware(stamp)
    if stamp is None:
        return True
    due = previous_slot(now - SLOT_GRACE)
    if due is None:  # no XNYS session in the lookback window: only a very old stamp is stale
        return now - stamp > timedelta(days=14)
    return stamp < due - SLOT_TOLERANCE


def source_service(country_code: str, source: str) -> str:
    """The monitored service (``deploy/monitor-services.yaml``) that reports a source."""
    if source == "ibkr":
        return "ibkr-snapshot"
    if country_code == "US":
        return "universe-us" if source == "universe" else "ingest-us"
    if country_code == "IN":
        return "ingest-india"
    return source


def source_stale(source: str, checked_at: datetime | None, now: datetime) -> bool:
    checked_at = aware(checked_at)
    if checked_at is None:
        return True
    if source in SLOT_SOURCES:
        return broker_snapshot_stale(checked_at, now)
    return (now - checked_at).total_seconds() > SOURCE_MAX_AGE_SECONDS


def source_state(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Judge one ``meta.source_status`` row: problem, stale and severe flags plus its service."""
    source = str(row["source"])
    status = str(row["status"])
    stale = source_stale(source, row.get("checked_at"), now)
    healthy = status == "ready" or status in IDLE_SOURCE_STATUSES.get(source, frozenset())
    return {
        "service_id": source_service(str(row["country_code"]), source),
        "stale": stale,
        "severe": status in SEVERE_SOURCE_STATUSES,
        "problem": stale or not healthy,
    }
