import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from factorlab.storage.clickhouse import ClickHouseStorage, _version, rows
from factorlab.storage.v2_india import V2IndiaStorage


class FakeClient:
    def __init__(self):
        self.inserts = []

    def insert(self, table, rows, column_names):
        self.inserts.append((table, [dict(zip(column_names, row, strict=True)) for row in rows]))


def test_live_versions_exceed_microsecond_versions_and_increase():
    now = datetime.now(UTC)
    first = _version(now)
    second = _version(now)
    assert first > int(now.timestamp() * 1_000_000)
    assert second > first


def test_rows_maps_columns_and_marks_naive_datetimes_utc():
    naive, aware = datetime(2026, 9, 24, 9, 15), datetime(2026, 9, 24, 9, 16, tzinfo=UTC)
    result = SimpleNamespace(column_names=["a", "b", "c"], result_rows=[(naive, aware, 3)])
    assert rows(result) == [{"a": naive.replace(tzinfo=UTC), "b": aware, "c": 3}]


def test_v2_ingestion_run_records_running_and_terminal_versions():
    client = FakeClient()
    storage = V2IndiaStorage(client)

    run = storage.start_ingestion_run(
        pipeline="india_intraday_1min",
        source="upstox",
        universe="demo",
        requested_series=10,
    )
    assert storage._active_run_id == run.run_id
    storage.finish_ingestion_run(
        run,
        status="partial",
        successful_series=9,
        failed_series=1,
        rows_written=3375,
    )

    assert [table for table, _ in client.inserts] == ["meta.ingestion_runs"] * 2
    running, terminal = (records[0] for _, records in client.inserts)
    assert running["status"] == "running" and running["country_code"] == "IN"
    assert terminal["run_id"] == run.run_id
    assert (terminal["status"], terminal["rows_written"]) == ("partial", 3375)
    assert terminal["version"] > running["version"]
    assert storage._active_run_id is None


def test_terminal_status_is_validated():
    storage = V2IndiaStorage(FakeClient())
    run = storage.start_ingestion_run(pipeline="p", source="s", market_code="USA")
    with pytest.raises(ValueError, match="Invalid terminal"):
        storage.finish_ingestion_run(run, status="running")


def test_base_storage_does_not_write_v1_run_tables():
    with pytest.raises(NotImplementedError):
        ClickHouseStorage(FakeClient()).start_ingestion_run(
            pipeline="p", source="s", market_code="IND"
        )


def test_run_handle_ids_are_unique():
    storage = V2IndiaStorage(FakeClient())
    first = storage.start_ingestion_run(pipeline="p", source="s")
    second = storage.start_ingestion_run(pipeline="p", source="s")
    assert isinstance(first.run_id, uuid.UUID) and first.run_id != second.run_id
