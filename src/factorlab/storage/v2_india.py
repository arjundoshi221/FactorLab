"""India live collection against canonical ClickHouse v2 tables."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from factorlab.storage.clickhouse import (
    _NO_CONTRACT_ID,
    ClickHouseStorage,
    IngestionRunHandle,
    _as_utc_datetime,
    _decimal,
    _integer,
    _version,
)
from factorlab.storage.v2_reference import UnresolvedReference, V2ReferenceWriter

IST = ZoneInfo("Asia/Kolkata")
MAX_UINT64 = 2**64 - 1


def _unsigned_count(value: Any) -> int | None:
    """Treat provider sentinels outside ClickHouse's UInt64 range as missing."""
    count = _integer(value)
    return count if count is not None and 0 <= count <= MAX_UINT64 else None


def _session(bar_time: datetime) -> str:
    local = bar_time.astimezone(IST).time()
    return "pre" if local < time(9, 15) else (
        "regular" if local < time(15, 30) else "post"
    )


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
            table, [[row[column] for column in columns] for row in records],
            column_names=columns,
        )

    def _identity_status(self, *, source: str, alias_kind: str, alias_value: str,
                         raw_id: uuid.UUID | None, reason: str = "",
                         target_kind: str | None = None,
                         target_id: uuid.UUID | None = None) -> None:
        now = datetime.now(UTC)
        self._insert_dicts("meta.unresolved_entities", [{
            "first_seen": now, "last_seen": now,
            "source": source, "alias_kind": alias_kind, "alias_value": alias_value,
            "scope_country": self.country_code, "scope_exchange": None,
            "context_json": json.dumps({"raw_id": str(raw_id) if raw_id else None}),
            "occurrence_count": 1, "retry_count": 0, "last_retry_at": now,
            "resolved_at": now if target_id else None,
            "resolved_target_kind": target_kind,
            "resolved_target_id": target_id,
            "resolved_by": "auto_resolver" if target_id else None,
            "resolution_note": reason or None,
            "version": _version(now), "ingested_at": now,
        }])

    def archive_http_response(
        self, *, source: str, source_url: str, response_body: bytes,
        status_code: int, response_headers: Mapping[str, str] | None = None,
        fetch_key: str = "", content_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
        fetched_at: datetime | None = None,
    ) -> uuid.UUID:
        now = fetched_at or datetime.now(UTC)
        raw_id = uuid.uuid4()
        self._insert_dicts("raw.archive", [{
            "raw_id": raw_id, "source": source, "source_channel": source,
            "transport": "http", "country_code": self.country_code, "source_url": source_url,
            "request_key": fetch_key, "status_code": status_code,
            "response_headers": json.dumps(dict(response_headers or {}), sort_keys=True),
            "response_body": gzip.compress(response_body), "content_type": content_type,
            "content_encoding": "gzip",
            "response_sha256": hashlib.sha256(response_body).hexdigest(),
            "fetched_at": now, "window_start_at": None, "event_count": None,
            "as_of_time": now,
            "metadata_json": json.dumps(dict(metadata or {}), sort_keys=True),
        }])
        return raw_id

    def seed_india_reference_data(self) -> None:
        """Refuse collection unless the migrated exchange and currency exist."""
        result = self.client.query(
            "SELECT count() FROM ref.exchanges FINAL "
            "WHERE exchange_code = 'NSE' AND country_code = 'IN' AND active"
        )
        if result.result_rows[0][0] != 1:
            raise UnresolvedReference("NSE reference is absent from ref.exchanges")

    def sync_instruments(
        self, instruments: Sequence[Mapping[str, Any]], *, raw_id: uuid.UUID | None = None,
    ) -> dict[str, uuid.UUID]:
        lookup: dict[str, uuid.UUID] = {}
        for item in instruments:
            if item.get("segment") != "NSE_EQ" or item.get("instrument_type") != "EQ":
                continue
            key = str(item["instrument_key"])
            try:
                _, _, listing_id = self.references.upsert_listing({
                    "instrument_key": key, "isin": item.get("isin"),
                    "country_code": "IN", "exchange_code": "NSE", "currency_code": "INR",
                    "trading_symbol": str(item["trading_symbol"]),
                    "name": item.get("name") or item["trading_symbol"],
                    "security_type": "common", "lot_size": item.get("lot_size") or 1,
                    "tick_size": _decimal(item.get("tick_size"), 6),
                }, alias_kind="upstox_instrument_key", alias_value=key, source="upstox")
            except UnresolvedReference as exc:
                self._identity_status(
                    source="upstox", alias_kind="upstox_instrument_key",
                    alias_value=key, raw_id=raw_id, reason=str(exc),
                )
                raise
            self._identity_status(source="upstox", alias_kind="upstox_instrument_key",
                                  alias_value=key, raw_id=raw_id,
                                  target_kind="listing", target_id=listing_id)
            lookup[str(item["trading_symbol"])] = listing_id
        return lookup

    def sync_contracts(
        self, instruments: Sequence[Mapping[str, Any]],
        instrument_lookup: Mapping[str, uuid.UUID], *, raw_id: uuid.UUID | None = None,
        instrument_keys: set[str] | None = None,
    ) -> dict[str, uuid.UUID]:
        from factorlab.storage.clickhouse import _epoch_ms_to_date

        lookup: dict[str, uuid.UUID] = {}
        for item in instruments:
            if item.get("segment") != "NSE_FO" or item.get("instrument_type") != "FUT":
                continue
            if instrument_keys is not None and str(item["instrument_key"]) not in instrument_keys:
                continue
            underlying = instrument_lookup.get(str(item.get("underlying_symbol") or ""))
            key = str(item["instrument_key"])
            try:
                if underlying is None:
                    raise UnresolvedReference(
                        f"future underlying unresolved: {item.get('underlying_symbol')}"
                    )
                lookup[key] = self.references.upsert_future({
                    "contract_key": key, "exchange_code": "NSE", "country_code": "IN",
                    "expiry": _epoch_ms_to_date(item.get("expiry")),
                    "lot_size": item.get("lot_size") or 1,
                    "tick_size": _decimal(item.get("tick_size"), 6),
                    "weekly": item.get("weekly", False),
                }, underlying_listing_id=underlying, source="upstox")
            except UnresolvedReference as exc:
                self._identity_status(source="upstox", alias_kind="upstox_instrument_key",
                                      alias_value=key, raw_id=raw_id, reason=str(exc))
                raise
            self._identity_status(source="upstox", alias_kind="upstox_instrument_key",
                                  alias_value=key, raw_id=raw_id,
                                  target_kind="contract", target_id=lookup[key])
        return lookup

    def start_ingestion_run(self, **kwargs: Any) -> IngestionRunHandle:
        handle = super().start_ingestion_run(**kwargs)
        self._active_run_id = handle.run_id
        return handle

    def finish_ingestion_run(self, handle: IngestionRunHandle, **kwargs: Any) -> None:
        super().finish_ingestion_run(handle, **kwargs)
        self._active_run_id = None

    def _write_ingestion_run(self, handle: IngestionRunHandle, *, status: str,
                             completed_at: datetime | None = None,
                             successful_series: int = 0, failed_series: int = 0,
                             rows_written: int = 0, error: str | None = None) -> None:
        now = datetime.now(UTC)
        country = {"IND": "IN", "USA": "US"}.get(handle.market_code, handle.market_code)
        self._insert_dicts("meta.ingestion_runs", [{
            "run_id": handle.run_id, "country_code": country,
            "pipeline": handle.pipeline, "source": handle.source,
            "source_channel": handle.source, "universe_id": handle.universe,
            "status": status, "started_at": handle.started_at,
            "completed_at": completed_at, "requested_series": handle.requested_series,
            "successful_series": successful_series, "failed_series": failed_series,
            "rows_written": rows_written, "listing_ids_touched": [],
            "error": error, "metadata_json": json.dumps(dict(handle.metadata), sort_keys=True),
            "parent_run_id": None, "version": _version(now), "ingested_at": now,
        }])

    def write_candles_1min(self, candles: pd.DataFrame, *, instrument_id: uuid.UUID,
                           symbol: str, contract_id: uuid.UUID | None = None,
                           source: str = "upstox", raw_id: uuid.UUID | None = None,
                           market_code: str = "IND") -> int:
        return self.write_candles_1min_batch([{
            "candles": candles, "instrument_id": instrument_id,
            "contract_id": contract_id, "symbol": symbol, "raw_id": raw_id,
        }], source=source, market_code=market_code)

    def write_candles_1min_batch(self, series_batches: Sequence[Mapping[str, Any]], *,
                                 source: str = "upstox", market_code: str = "IND") -> int:
        if self._active_run_id is None:
            raise RuntimeError("start an ingestion run before writing v2 bars")
        now = datetime.now(UTC)
        bars: list[dict[str, Any]] = []
        futures: list[dict[str, Any]] = []
        for item in series_batches:
            candles = item["candles"]
            if candles.empty:
                continue
            listing_id = uuid.UUID(str(item["instrument_id"]))
            ref = self.client.query(
                "SELECT l.security_id, s.entity_id, s.security_type "
                "FROM ref.listings AS l FINAL INNER JOIN ref.securities AS s FINAL "
                "ON s.security_id = l.security_id "
                "WHERE l.listing_id = {listing_id:UUID} AND l.country_code = 'IN'",
                parameters={"listing_id": listing_id},
            ).result_rows
            if len(ref) != 1:
                raise UnresolvedReference(f"listing/security unresolved: {listing_id}")
            contract = item.get("contract_id")
            if contract:
                match = self.client.query(
                    "SELECT contract_id FROM ref.contracts FINAL "
                    "WHERE contract_id = {contract_id:UUID} "
                    "AND underlying_listing_id = {listing_id:UUID}",
                    parameters={"contract_id": contract, "listing_id": listing_id},
                ).result_rows
                if len(match) != 1:
                    raise UnresolvedReference(f"future contract unresolved: {contract}")
            for candle in candles.to_dict("records"):
                bar_time = _as_utc_datetime(candle["timestamp"])
                common = {
                    "country_code": "IN", "resolution": "1min",
                    "session": _session(bar_time), "bar_time": bar_time,
                    "trade_date": bar_time.astimezone(IST).date(),
                    **{key: _decimal(candle.get(key), 6)
                       for key in ("open", "high", "low", "close")},
                    "volume": _unsigned_count(candle.get("volume")),
                    "oi": _unsigned_count(candle.get("oi")), "source": source,
                    "raw_id": item.get("raw_id"), "ingest_run_id": self._active_run_id,
                    "as_of_time": now, "ingested_at": now, "version": _version(now),
                }
                if contract:
                    futures.append({
                        "country_code": "IN", "underlying_listing_id": listing_id,
                        "contract_id": contract, "source_symbol": item["symbol"],
                        **{key: value for key, value in common.items() if key != "country_code"},
                    })
                else:
                    bars.append({
                        "country_code": "IN", "listing_id": listing_id,
                        "security_id": ref[0][0], "entity_id": ref[0][1],
                        "product_type": str(ref[0][2]),
                        **{key: value for key, value in common.items() if key != "country_code"},
                        "turnover": None, "trades_count": None,
                        "settlement_price": None, "source_channel": "upstox_candles",
                        "latency_ms": None,
                    })
        self._insert_dicts("market.bars", bars)
        self._insert_dicts("market.futures_contract_bars", futures)
        return len(bars) + len(futures)

    def latest_candle_times(
        self, series: Sequence[Mapping[str, Any]], *, source: str = "upstox",
        before: datetime | None = None,
    ) -> dict[tuple[uuid.UUID, uuid.UUID], datetime]:
        requested = {
            (uuid.UUID(str(item["instrument_id"])),
             uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID)))
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
        self, series: Sequence[Mapping[str, Any]], *, source: str = "upstox",
        universe: str = "default", resolution: str = "1min",
    ) -> int:
        now = datetime.now(UTC)
        version = _version(now)
        current = {
            (uuid.UUID(str(item["instrument_id"])),
             uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID))): item
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
            records.append({
                "country_code": "IN", "listing_id": listing_id,
                "contract_id": None if contract_id == _NO_CONTRACT_ID else contract_id,
                "legacy_instrument_id": None, "legacy_contract_id": None,
                "source_table": None, "symbol": str(item["symbol"]),
                "provider_symbol": None, "source": source, "universe": universe,
                "resolution": resolution, "active": True, "source_hash": None,
                "version": version, "ingested_at": now, "migrated_at": None,
            })
        for listing_id, contract_id, symbol, old_universe, old_resolution in existing:
            key = (listing_id, contract_id or _NO_CONTRACT_ID)
            if key in current:
                continue
            records.append({
                "country_code": "IN", "listing_id": listing_id,
                "contract_id": contract_id, "legacy_instrument_id": None,
                "legacy_contract_id": None, "source_table": None,
                "symbol": symbol, "provider_symbol": None, "source": source,
                "universe": old_universe, "resolution": old_resolution,
                "active": False, "source_hash": None, "version": version,
                "ingested_at": now, "migrated_at": None,
            })
        self._insert_dicts("meta.expected_series", records)
        return len(current)
