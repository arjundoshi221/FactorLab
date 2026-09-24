"""Shared provider contract: RawCapture/Provenance validation and ingestion_run outcomes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from factorlab.shared.ingest.provider import (
    NullProviderStorage,
    ProviderStorage,
    RawCapture,
    RunContext,
    ingestion_run,
    run_provider,
)

NOW = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Handle:
    run_id: uuid.UUID


class RecordingStorage:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.finished: list[dict] = []
        self.archived: list[tuple] = []

    def archive_raw(self, capture, *, source, source_channel):
        self.archived.append((capture, source, source_channel))
        return uuid.uuid4()

    def start_ingestion_run(self, **kwargs):
        self.started.append(kwargs)
        return Handle(uuid.uuid4())

    def finish_ingestion_run(self, handle, **kwargs):
        self.finished.append({"run_id": handle.run_id, **kwargs})


def _capture(**overrides) -> RawCapture:
    return RawCapture(**{"body": b"{}", "request_key": "k", "transport": "tcp_socket",
                         "fetched_at": NOW, **overrides})


def test_raw_capture_requires_bytes_and_aware_time():
    with pytest.raises(TypeError):
        _capture(body="not-bytes")
    with pytest.raises(ValueError):
        _capture(fetched_at=datetime(2026, 9, 24))  # noqa: DTZ001 - naive on purpose


def test_storage_protocols_are_structural():
    assert isinstance(RecordingStorage(), ProviderStorage)
    assert isinstance(NullProviderStorage(), ProviderStorage)


def test_run_succeeds_with_all_units_ok_and_stamps_provenance():
    storage = RecordingStorage()
    with ingestion_run(storage, pipeline="p", source="ibkr", market_code="USA") as ctx:
        raw_id = ctx.archive(_capture(), source_channel="paper_gateway")
        provenance = ctx.provenance(source_channel="paper_gateway", raw_id=raw_id, as_of_time=NOW)
        ctx.succeed_unit("paper:positions", 3)
        ctx.succeed_unit("paper:executions", 0)

    assert storage.archived[0][1:] == ("ibkr", "paper_gateway")
    assert provenance.ingest_run_id == ctx.run_id
    assert provenance.columns()["raw_id"] == raw_id
    assert storage.started[0]["market_code"] == "USA"
    assert storage.finished == [{
        "run_id": ctx.run_id, "status": "success", "successful_series": 2,
        "failed_series": 0, "rows_written": 3, "error": None,
    }]


def test_run_is_partial_when_some_units_fail_and_errors_are_redacted():
    storage = RecordingStorage()
    with ingestion_run(storage, pipeline="p", source="ibkr", market_code="USA") as ctx:
        ctx.succeed_unit("paper:positions", 2)
        ctx.fail_unit("live:connect", ConnectionError("https://x.test/?token=secret refused"))

    finished = storage.finished[0]
    assert finished["status"] == "partial"
    assert finished["failed_series"] == 1
    assert "secret" not in finished["error"]
    assert "live:connect: ConnectionError" in finished["error"]


def test_run_is_failed_when_every_unit_fails():
    storage = RecordingStorage()
    with ingestion_run(storage, pipeline="p", source="ibkr", market_code="USA") as ctx:
        ctx.fail_unit("paper:connect", "down")
    assert storage.finished[0]["status"] == "failed"


def test_escaping_exception_marks_run_failed_and_reraises():
    storage = RecordingStorage()
    with pytest.raises(RuntimeError), ingestion_run(
        storage, pipeline="p", source="ibkr", market_code="USA",
    ) as ctx:
        ctx.succeed_unit("paper:positions", 1)
        raise RuntimeError("clickhouse unavailable")
    finished = storage.finished[0]
    assert finished["status"] == "failed"
    assert finished["rows_written"] == 1
    assert finished["error"] == "RuntimeError: clickhouse unavailable"


def test_interrupt_marks_run_cancelled():
    storage = RecordingStorage()
    with pytest.raises(KeyboardInterrupt), ingestion_run(
        storage, pipeline="p", source="ibkr", market_code="USA",
    ):
        raise KeyboardInterrupt
    assert storage.finished[0]["status"] == "cancelled"


def test_run_provider_returns_summary():
    class Provider:
        source = "demo"
        pipeline = "demo_pipeline"
        market_code = "USA"

        def collect(self, ctx: RunContext) -> None:
            ctx.succeed_unit("a", 5)
            ctx.fail_unit("b", "boom")

    storage = RecordingStorage()
    summary = run_provider(Provider(), storage, universe="u", metadata={"k": "v"})
    assert summary.status == "partial"
    assert summary.rows_written == 5
    assert [u.name for u in summary.failed_units] == ["b"]
    assert storage.started[0]["pipeline"] == "demo_pipeline"
    assert storage.started[0]["metadata"] == {"k": "v"}


def test_null_storage_records_without_writing():
    storage = NullProviderStorage()
    with ingestion_run(storage, pipeline="p", source="s", market_code="USA") as ctx:
        ctx.archive(_capture(), source_channel="c")
    assert len(storage.archived) == 1
    assert storage.finished[0]["status"] == "success"
