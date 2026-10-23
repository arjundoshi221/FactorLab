"""Schedule-aware IBKR freshness and source-to-service mapping."""

from datetime import UTC, datetime, timedelta

from factorlab.components.api.catalog import pipeline_status
from factorlab.components.api.catalog_pipelines import PIPELINES_BY_ID
from factorlab.components.api.monitoring_errors import PIPELINE_SERVICES
from factorlab.components.api.source_health import (
    broker_snapshot_stale,
    ibkr_slots,
    source_service,
    source_state,
)

# Default slots: 06:00 and 16:30 New York. Thu 2026-09-24 is EDT (UTC-4).
THU_0600 = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
THU_1630 = datetime(2026, 9, 24, 20, 30, tzinfo=UTC)


def test_default_slots_snapshot_is_fresh_between_slots_and_over_weekend(monkeypatch):
    monkeypatch.delenv("IBKR_SNAPSHOT_TIMES", raising=False)
    # Midday Thursday: the 06:00 snapshot is still current (a fixed 2h rule called this stale).
    assert not broker_snapshot_stale(THU_0600 + timedelta(minutes=2), THU_0600 + timedelta(hours=6))
    # Sunday: Friday's 16:30 snapshot is the latest one expected.
    friday_close = datetime(2026, 9, 25, 20, 31, tzinfo=UTC)
    assert not broker_snapshot_stale(friday_close, datetime(2026, 9, 27, 15, 0, tzinfo=UTC))


def test_missed_slot_is_stale_only_after_grace(monkeypatch):
    monkeypatch.delenv("IBKR_SNAPSHOT_TIMES", raising=False)
    morning = THU_0600 + timedelta(minutes=1)
    assert not broker_snapshot_stale(morning, THU_1630 + timedelta(minutes=30))
    assert broker_snapshot_stale(morning, THU_1630 + timedelta(minutes=61))
    assert broker_snapshot_stale(None, THU_1630)


def test_hourly_override_is_respected(monkeypatch):
    monkeypatch.setenv("IBKR_SNAPSHOT_TIMES", ",".join(f"{h:02d}:00" for h in range(24)))
    assert len(ibkr_slots()) == 24
    now = THU_0600 + timedelta(hours=5, minutes=30)  # 11:30 New York
    assert not broker_snapshot_stale(THU_0600 + timedelta(hours=5), now)
    assert broker_snapshot_stale(THU_0600 + timedelta(hours=3), now)


def test_invalid_override_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("IBKR_SNAPSHOT_TIMES", "nonsense")
    assert [slot.isoformat("minutes") for slot in ibkr_slots()] == ["06:00", "16:30"]


def test_ibkr_source_maps_to_its_own_service_and_waiting_is_normal(monkeypatch):
    monkeypatch.delenv("IBKR_SNAPSHOT_TIMES", raising=False)
    assert source_service("US", "ibkr") == "ibkr-snapshot"
    assert source_service("US", "schwab") == "ingest-us"
    assert source_service("US", "universe") == "universe-us"
    assert source_service("US", "schwab_futures") == "futures-us"
    assert source_service("IN", "upstox") == "ingest-india"
    waiting = {"country_code": "US", "source": "ibkr", "status": "waiting"}
    state = source_state({**waiting, "checked_at": THU_0600}, THU_0600 + timedelta(hours=4))
    assert state == {
        "service_id": "ibkr-snapshot",
        "stale": False,
        "severe": False,
        "problem": False,
    }
    failed = source_state({**waiting, "status": "incomplete", "checked_at": THU_0600}, THU_0600)
    assert failed["problem"] and failed["severe"]


def test_other_sources_keep_the_ten_minute_rule():
    now = THU_0600
    row = {"country_code": "US", "source": "schwab", "status": "ready"}
    assert not source_state({**row, "checked_at": now - timedelta(minutes=9)}, now)["problem"]
    assert source_state({**row, "checked_at": now - timedelta(minutes=11)}, now)["stale"]
    # "waiting" is only normal for slot-scheduled sources.
    assert source_state({**row, "status": "waiting", "checked_at": now}, now)["problem"]


def test_ibkr_pipeline_is_catalogued_and_slot_aware(monkeypatch):
    monkeypatch.delenv("IBKR_SNAPSHOT_TIMES", raising=False)
    assert PIPELINE_SERVICES["ibkr_broker_snapshot"] == "ibkr-snapshot"
    pipeline = PIPELINES_BY_ID["ibkr_broker_snapshot"]
    values = {"last_status": "success"}
    late_evening = THU_1630 + timedelta(hours=3)
    healthy = pipeline_status(pipeline, values, THU_1630, [], late_evening)
    assert healthy[0] == "healthy"  # outside market hours, but not "not expected"
    missed = pipeline_status(pipeline, values, THU_0600, [], late_evening)
    assert missed[0] == "attention"
    assert pipeline_status(pipeline, {}, None, [], late_evening)[0] == "missing"


def test_futures_recorder_is_healthy_while_globex_is_closed():
    now = THU_0600
    closed = {"country_code": "US", "source": "schwab_futures", "status": "waiting"}
    state = source_state({**closed, "checked_at": now - timedelta(minutes=5)}, now)
    assert state["service_id"] == "futures-us" and not state["problem"]
    assert source_state({**closed, "status": "ok", "checked_at": now}, now)["problem"]
