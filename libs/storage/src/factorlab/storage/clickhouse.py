"""Base class for the ClickHouse v2 writers: connection plus the ingestion-run lifecycle.

The connection itself and the pandas-free value helpers live in
``factorlab.clickhouse``; they are re-exported here for the writers that import them
from this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from factorlab.clickhouse import ClickHouse, rows
from factorlab.clickhouse.values import decoded_text as _decoded_text
from factorlab.clickhouse.values import version as _version
from factorlab.core.logging import bind, unbind

_NO_CONTRACT_ID = uuid.UUID(int=0)


@dataclass(frozen=True)
class IngestionRunHandle:
    """Identity and immutable fields needed to complete an ingestion run."""

    run_id: uuid.UUID
    market_code: str
    pipeline: str
    source: str
    universe: str
    started_at: datetime
    requested_series: int
    metadata: Mapping[str, Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ClickHouseStorage(ClickHouse):
    """A ClickHouse connection plus the ingestion-run lifecycle the v2 writers extend."""

    def start_ingestion_run(
        self,
        *,
        pipeline: str,
        source: str,
        market_code: str = "IND",
        universe: str = "",
        requested_series: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionRunHandle:
        """Record the start of an ingestion run."""

        handle = IngestionRunHandle(
            run_id=uuid.uuid4(),
            market_code=market_code,
            pipeline=pipeline,
            source=source,
            universe=universe,
            started_at=_utc_now(),
            requested_series=requested_series,
            metadata=dict(metadata or {}),
        )
        self._write_ingestion_run(handle, status="running")
        # Log lines emitted until the run finishes carry its id (meta.ingestion_runs.run_id).
        bind(run_id=str(handle.run_id), pipeline=pipeline, source=source)
        return handle

    def finish_ingestion_run(
        self,
        handle: IngestionRunHandle,
        *,
        status: str,
        successful_series: int = 0,
        failed_series: int = 0,
        rows_written: int = 0,
        error: str | None = None,
    ) -> None:
        """Write the terminal version of an ingestion run."""

        if status not in {"success", "partial", "failed", "cancelled"}:
            raise ValueError(f"Invalid terminal ingestion status: {status}")
        self._write_ingestion_run(
            handle,
            status=status,
            completed_at=_utc_now(),
            successful_series=successful_series,
            failed_series=failed_series,
            rows_written=rows_written,
            error=error,
        )
        unbind("run_id", "pipeline", "source")

    def _write_ingestion_run(
        self,
        handle: IngestionRunHandle,
        *,
        status: str,
        completed_at: datetime | None = None,
        successful_series: int = 0,
        failed_series: int = 0,
        rows_written: int = 0,
        error: str | None = None,
    ) -> None:
        """Persist one version of a run; the v2 writers record ``meta.ingestion_runs``."""

        raise NotImplementedError("use a v2 storage class (V2IndiaStorage and subclasses)")


def _decimal(value: Any, scale: int) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))


def _integer(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _epoch_ms_to_date(value: Any) -> date | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date()


def _as_utc_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").to_pydatetime()


__all__ = [
    "ClickHouseStorage",
    "IngestionRunHandle",
    "_decoded_text",
    "_version",
    "rows",
]
