"""India live collection against canonical ClickHouse v2 tables."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from factorlab.ingest.provider import RawCapture
from factorlab.storage.clickhouse import (
    _NO_CONTRACT_ID,
    ClickHouseStorage,
    IngestionRunHandle,
    _version,
)
from factorlab.storage.v2_reference import UnresolvedReference, V2ReferenceWriter

# How far back an identical capture of the same request is reused instead of re-archived.
RAW_REUSE_DAYS = 30


class V2IndiaStorage(ClickHouseStorage):
    country_code = "IN"

    def __init__(self, client: Any, tunnel: Any = None) -> None:
        super().__init__(client, tunnel)
        self.references = V2ReferenceWriter(client)
        self._active_run_id: uuid.UUID | None = None

    def _insert_dicts(self, table: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        columns = list(records[0])
        self.client.insert(
            table,
            [[row[column] for column in columns] for row in records],
            column_names=columns,
        )

    def _identity_status(
        self,
        *,
        source: str,
        alias_kind: str,
        alias_value: str,
        raw_id: uuid.UUID | None,
        reason: str = "",
        target_kind: str | None = None,
        target_id: uuid.UUID | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self._insert_dicts(
            "meta.unresolved_entities",
            [
                {
                    "first_seen": now,
                    "last_seen": now,
                    "source": source,
                    "alias_kind": alias_kind,
                    "alias_value": alias_value,
                    "scope_country": self.country_code,
                    "scope_exchange": None,
                    "context_json": json.dumps({"raw_id": str(raw_id) if raw_id else None}),
                    "occurrence_count": 1,
                    "retry_count": 0,
                    "last_retry_at": now,
                    "resolved_at": now if target_id else None,
                    "resolved_target_kind": target_kind,
                    "resolved_target_id": target_id,
                    "resolved_by": "auto_resolver" if target_id else None,
                    "resolution_note": reason or None,
                    "version": _version(now),
                    "ingested_at": now,
                }
            ],
        )

    def archive_raw(self, capture: RawCapture, *, source: str, source_channel: str) -> uuid.UUID:
        """Persist one provider response to immutable ``raw.archive`` and return its id.

        A byte-identical response to the same request within ``RAW_REUSE_DAYS`` reuses
        the stored capture: replay stays exact and weekly history refreshes stop
        re-archiving the same multi-decade payloads.
        """
        sha256 = hashlib.sha256(capture.body).hexdigest()
        stored = self.client.query(
            "SELECT raw_id FROM raw.archive WHERE source = {source:String} "
            "AND country_code = {country:String} AND source_channel = {channel:String} "
            "AND request_key = {key:String} AND response_sha256 = {sha256:String} "
            "AND fetched_at >= {since:DateTime64(3, 'UTC')} "
            "AND fetched_at <= {fetched:DateTime64(3, 'UTC')} "
            "ORDER BY fetched_at DESC LIMIT 1",
            parameters={
                "source": source,
                "country": self.country_code,
                "channel": source_channel,
                "key": capture.request_key,
                "sha256": sha256,
                "since": capture.fetched_at - timedelta(days=RAW_REUSE_DAYS),
                "fetched": capture.fetched_at,
            },
        ).result_rows
        if stored:
            return uuid.UUID(str(stored[0][0]))
        raw_id = uuid.uuid4()
        self._insert_dicts(
            "raw.archive",
            [
                {
                    "raw_id": raw_id,
                    "source": source,
                    "source_channel": source_channel,
                    "transport": capture.transport,
                    "country_code": self.country_code,
                    "source_url": capture.source_url,
                    "request_key": capture.request_key,
                    "status_code": capture.status_code,
                    "response_headers": json.dumps(dict(capture.headers), sort_keys=True),
                    "response_body": gzip.compress(capture.body),
                    "content_type": capture.content_type,
                    "content_encoding": "gzip",
                    "response_sha256": sha256,
                    "fetched_at": capture.fetched_at,
                    "window_start_at": None,
                    "event_count": None,
                    "as_of_time": capture.fetched_at,
                    "metadata_json": json.dumps(dict(capture.metadata), sort_keys=True),
                }
            ],
        )
        return raw_id

    def archive_http_response(
        self,
        *,
        source: str,
        source_url: str,
        response_body: bytes,
        status_code: int,
        response_headers: Mapping[str, str] | None = None,
        fetch_key: str = "",
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
        fetched_at: datetime | None = None,
    ) -> uuid.UUID:
        return self.archive_raw(
            RawCapture(
                body=response_body,
                request_key=fetch_key,
                transport="http",
                fetched_at=fetched_at or datetime.now(UTC),
                source_url=source_url,
                content_type=content_type,
                status_code=status_code,
                headers=dict(response_headers or {}),
                metadata=dict(metadata or {}),
            ),
            source=source,
            source_channel=source,
        )

    def seed_india_reference_data(self) -> None:
        """Refuse collection unless the migrated exchange and currency exist."""
        result = self.client.query(
            "SELECT count() FROM ref.exchanges FINAL "
            "WHERE exchange_code = 'NSE' AND country_code = 'IN' AND active"
        )
        if result.result_rows[0][0] != 1:
            raise UnresolvedReference("NSE reference is absent from ref.exchanges")

    def start_ingestion_run(self, **kwargs: Any) -> IngestionRunHandle:
        handle = super().start_ingestion_run(**kwargs)
        self._active_run_id = handle.run_id
        return handle

    def finish_ingestion_run(self, handle: IngestionRunHandle, **kwargs: Any) -> None:
        super().finish_ingestion_run(handle, **kwargs)
        self._active_run_id = None

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
        now = datetime.now(UTC)
        country = {"IND": "IN", "USA": "US"}.get(handle.market_code, handle.market_code)
        self._insert_dicts(
            "meta.ingestion_runs",
            [
                {
                    "run_id": handle.run_id,
                    "country_code": country,
                    "pipeline": handle.pipeline,
                    "source": handle.source,
                    "source_channel": handle.source,
                    "universe_id": handle.universe,
                    "status": status,
                    "started_at": handle.started_at,
                    "completed_at": completed_at,
                    "requested_series": handle.requested_series,
                    "successful_series": successful_series,
                    "failed_series": failed_series,
                    "rows_written": rows_written,
                    "listing_ids_touched": [],
                    "error": error,
                    "metadata_json": json.dumps(dict(handle.metadata), sort_keys=True),
                    "parent_run_id": None,
                    "version": _version(now),
                    "ingested_at": now,
                }
            ],
        )

    def latest_candle_times(
        self,
        series: Sequence[Mapping[str, Any]],
        *,
        source: str = "upstox",
        before: datetime | None = None,
    ) -> dict[tuple[uuid.UUID, uuid.UUID], datetime]:
        requested = {
            (
                uuid.UUID(str(item["instrument_id"])),
                uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID)),
            )
            for item in series
        }
        if not requested:
            return {}
        parameters: dict[str, Any] = {"source": source}
        cutoff = "AND bar_time < {before:DateTime64(3, 'UTC')}" if before else ""
        if before:
            parameters["before"] = before
        latest: dict[tuple[uuid.UUID, uuid.UUID], datetime] = {}
        equity_ids = [listing for listing, contract in requested if contract == _NO_CONTRACT_ID]
        if equity_ids:
            result = self.client.query(
                "SELECT listing_id, max(bar_time) FROM market.bars FINAL "
                "WHERE country_code = 'IN' AND resolution = '1min' "
                "AND source = {source:String} AND listing_id IN {ids:Array(UUID)} "
                f"{cutoff} GROUP BY listing_id",
                parameters={**parameters, "ids": equity_ids},
            )
            latest.update({(row[0], _NO_CONTRACT_ID): row[1] for row in result.result_rows})
        contract_ids = [contract for _, contract in requested if contract != _NO_CONTRACT_ID]
        if contract_ids:
            result = self.client.query(
                "SELECT underlying_listing_id, contract_id, max(bar_time) "
                "FROM market.futures_contract_bars FINAL "
                "WHERE country_code = 'IN' AND resolution = '1min' "
                "AND source = {source:String} AND contract_id IN {ids:Array(UUID)} "
                f"{cutoff} GROUP BY underlying_listing_id, contract_id",
                parameters={**parameters, "ids": contract_ids},
            )
            latest.update({(row[0], row[1]): row[2] for row in result.result_rows})
        return {key: value for key, value in latest.items() if key in requested}

    def sync_expected_india_series(
        self,
        series: Sequence[Mapping[str, Any]],
        *,
        source: str = "upstox",
        universe: str = "default",
        resolution: str = "1min",
    ) -> int:
        now = datetime.now(UTC)
        version = _version(now)
        current = {
            (
                uuid.UUID(str(item["instrument_id"])),
                uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID)),
            ): item
            for item in series
        }
        existing = self.client.query(
            "SELECT listing_id, contract_id, symbol, universe, resolution "
            "FROM meta.expected_series FINAL "
            "WHERE country_code = 'IN' AND source = {source:String} "
            "AND resolution = {resolution:String} AND active",
            parameters={"source": source, "resolution": resolution},
        ).result_rows
        records: list[dict[str, Any]] = []
        for (listing_id, contract_id), item in current.items():
            records.append(
                {
                    "country_code": "IN",
                    "listing_id": listing_id,
                    "contract_id": None if contract_id == _NO_CONTRACT_ID else contract_id,
                    "legacy_instrument_id": None,
                    "legacy_contract_id": None,
                    "source_table": None,
                    "symbol": str(item["symbol"]),
                    "provider_symbol": None,
                    "source": source,
                    "universe": universe,
                    "resolution": resolution,
                    "active": True,
                    "source_hash": None,
                    "version": version,
                    "ingested_at": now,
                    "migrated_at": None,
                }
            )
        for listing_id, contract_id, symbol, old_universe, old_resolution in existing:
            key = (listing_id, contract_id or _NO_CONTRACT_ID)
            if key in current:
                continue
            records.append(
                {
                    "country_code": "IN",
                    "listing_id": listing_id,
                    "contract_id": contract_id,
                    "legacy_instrument_id": None,
                    "legacy_contract_id": None,
                    "source_table": None,
                    "symbol": symbol,
                    "provider_symbol": None,
                    "source": source,
                    "universe": old_universe,
                    "resolution": old_resolution,
                    "active": False,
                    "source_hash": None,
                    "version": version,
                    "ingested_at": now,
                    "migrated_at": None,
                }
            )
        self._insert_dicts("meta.expected_series", records)
        return len(current)
