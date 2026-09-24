"""ClickHouse storage for the raw, reference, and market migration slice."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import clickhouse_connect
import pandas as pd

from factorlab.core.secrets import get_secret

_INSTRUMENT_NAMESPACE = uuid.UUID("c53d03b7-4fd7-43c8-915e-d503a04f6c9e")
_CONTRACT_NAMESPACE = uuid.UUID("8a72760e-e469-4602-a0b4-67e3f61a3b3b")
_NO_CONTRACT_ID = uuid.UUID(int=0)
_VERSION_LOCK = threading.Lock()
_LAST_VERSION = 0


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
    return datetime.now(timezone.utc)


def _version(timestamp: datetime) -> int:
    # Migration/resolver versions use nanoseconds. A live replacement must be
    # greater even when a caller supplies an older source timestamp.
    global _LAST_VERSION
    with _VERSION_LOCK:
        _LAST_VERSION = max(
            _LAST_VERSION + 1, time.time_ns(), int(timestamp.timestamp() * 1_000_000_000)
        )
        return _LAST_VERSION


def instrument_id_for(instrument_key: str) -> uuid.UUID:
    """Return a stable internal UUID for a vendor instrument key."""
    return uuid.uuid5(_INSTRUMENT_NAMESPACE, instrument_key)


def contract_id_for(contract_key: str) -> uuid.UUID:
    """Return a stable internal UUID for a vendor contract key."""
    return uuid.uuid5(_CONTRACT_NAMESPACE, contract_key)


class ClickHouseStorage:
    """Thin explicit-DDL storage client for the initial ClickHouse migration."""

    def __init__(self, client: Any, tunnel: Any = None) -> None:
        self.client = client
        self._tunnel = tunnel

    @classmethod
    def from_environment(cls) -> "ClickHouseStorage":
        ssh_host = os.getenv("CLICKHOUSE_SSH_HOST")
        remote_host = os.getenv("CLICKHOUSE_HOST", "localhost")
        remote_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
        tunnel = None
        client_host = remote_host
        client_port = remote_port
        if ssh_host:
            tunnel = _open_ssh_tunnel(ssh_host, remote_host, remote_port)
            client_host = "127.0.0.1"
            client_port = tunnel.local_bind_port
        try:
            client = clickhouse_connect.get_client(
                host=client_host,
                port=client_port,
                username=os.getenv("CLICKHOUSE_USERNAME", "factorlab"),
                password=get_secret("CLICKHOUSE_PASSWORD", "factorlab_dev"),
                database=os.getenv("CLICKHOUSE_DATABASE", "factorlab"),
            )
        except Exception:
            if tunnel is not None:
                tunnel.stop()
            raise
        return cls(client, tunnel=tunnel)

    def close(self) -> None:
        """Close the ClickHouse client and any SSH tunnel opened for it."""
        try:
            self.client.close()
        except Exception:
            pass
        if self._tunnel is not None:
            try:
                self._tunnel.stop()
            except Exception:
                pass
            self._tunnel = None

    def __enter__(self) -> "ClickHouseStorage":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

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
        """Persist an exact response body compressed with gzip for replay."""
        timestamp = fetched_at or _utc_now()
        raw_id = uuid.uuid4()
        self.client.insert(
            "raw_http_archive",
            [[
                raw_id,
                source,
                source_url,
                fetch_key,
                status_code,
                json.dumps(dict(response_headers or {}), sort_keys=True),
                gzip.compress(response_body),
                content_type,
                "gzip",
                hashlib.sha256(response_body).hexdigest(),
                timestamp,
                timestamp,
                json.dumps(dict(metadata or {}), sort_keys=True),
            ]],
            column_names=[
                "raw_id", "source", "source_url", "fetch_key", "status_code",
                "response_headers", "response_body", "content_type", "content_encoding",
                "response_sha256", "fetched_at", "as_of_time", "metadata_json",
            ],
        )
        return raw_id

    def seed_india_reference_data(self) -> None:
        """Seed the minimum static records required for NSE/Upstox ingestion."""
        timestamp = _utc_now()
        version = _version(timestamp)
        self.client.insert(
            "ref_countries",
            [["IN", "India", "asia", "Asia/Kolkata", "manual_seed", version, timestamp]],
            column_names=["country_code", "name", "region", "timezone", "source", "version", "ingested_at"],
        )
        self.client.insert(
            "ref_exchanges",
            [["NSE", "National Stock Exchange of India", "IN", "IND", "INR", "Asia/Kolkata", "manual_seed", version, timestamp]],
            column_names=[
                "exchange_code", "name", "country_code", "market_code", "currency_code",
                "timezone", "source", "version", "ingested_at",
            ],
        )

    def sync_instruments(
        self,
        instruments: Sequence[Mapping[str, Any]],
        *,
        raw_id: uuid.UUID | None = None,
    ) -> dict[str, uuid.UUID]:
        """Upsert NSE equities from an Upstox instrument-master payload."""
        timestamp = _utc_now()
        version = _version(timestamp)
        today = timestamp.date()
        result = self.client.query(
            """
            SELECT instrument_id, instrument_key, trading_symbol, name, isin,
                   exchange_code, segment, instrument_type, asset_class, country_code,
                   market_code, currency_code, lot_size, tick_size, freeze_quantity,
                   exchange_token, status, first_seen, last_seen, source, raw_id
            FROM ref_instruments FINAL
            WHERE source = 'upstox' AND segment = 'NSE_EQ' AND instrument_type = 'EQ'
            """
        )
        existing = {str(row[1]): row for row in result.result_rows}
        rows: list[list[Any]] = []
        lookup: dict[str, uuid.UUID] = {}
        current_keys: set[str] = set()

        for record in instruments:
            if record.get("segment") != "NSE_EQ" or record.get("instrument_type") != "EQ":
                continue
            instrument_key = str(record["instrument_key"])
            current_keys.add(instrument_key)
            instrument_id = instrument_id_for(instrument_key)
            symbol = str(record["trading_symbol"])
            lookup[symbol] = instrument_id
            rows.append([
                instrument_id,
                instrument_key,
                symbol,
                str(record.get("name") or symbol),
                record.get("isin") or None,
                "NSE",
                "NSE_EQ",
                "EQ",
                "equity",
                "IN",
                "IND",
                "INR",
                int(record.get("lot_size") or 1),
                _decimal(record.get("tick_size"), 6),
                _decimal(record.get("freeze_quantity"), 3),
                str(record["exchange_token"]) if record.get("exchange_token") else None,
                "active",
                existing[instrument_key][17] if instrument_key in existing else today,
                today,
                "upstox",
                raw_id,
                version,
                timestamp,
            ])

        # Keep removed/delisted instruments queryable as historical references,
        # but do not present them as members of today's collectable universe.
        for instrument_key, old in existing.items():
            if instrument_key in current_keys or str(old[16]) == "inactive":
                continue
            rows.append([
                old[0], old[1], old[2], old[3], old[4], old[5], old[6], old[7],
                old[8], _decoded_text(old[9]), old[10], _decoded_text(old[11]),
                old[12], old[13], old[14],
                old[15], "inactive", old[17], old[18], old[19], old[20], version,
                timestamp,
            ])

        if rows:
            self.client.insert(
                "ref_instruments",
                rows,
                column_names=[
                    "instrument_id", "instrument_key", "trading_symbol", "name", "isin",
                    "exchange_code", "segment", "instrument_type", "asset_class", "country_code",
                    "market_code", "currency_code", "lot_size", "tick_size", "freeze_quantity",
                    "exchange_token", "status", "first_seen", "last_seen", "source", "raw_id",
                    "version", "ingested_at",
                ],
            )
        return lookup

    def sync_contracts(
        self,
        instruments: Sequence[Mapping[str, Any]],
        instrument_lookup: Mapping[str, uuid.UUID],
        *,
        raw_id: uuid.UUID | None = None,
    ) -> dict[str, uuid.UUID]:
        """Upsert NSE futures whose underlyings have been synced as equities."""
        timestamp = _utc_now()
        version = _version(timestamp)
        today = timestamp.date()
        rows: list[list[Any]] = []
        lookup: dict[str, uuid.UUID] = {}

        for record in instruments:
            if record.get("segment") != "NSE_FO" or record.get("instrument_type") != "FUT":
                continue
            instrument_id = instrument_lookup.get(str(record.get("underlying_symbol") or ""))
            if instrument_id is None:
                continue
            contract_key = str(record["instrument_key"])
            contract_id = contract_id_for(contract_key)
            lookup[contract_key] = contract_id
            rows.append([
                contract_id,
                contract_key,
                instrument_id,
                str(record.get("trading_symbol") or ""),
                "FUT",
                "NSE_FO",
                _epoch_ms_to_date(record.get("expiry")),
                _decimal(record.get("strike_price"), 2),
                int(record.get("lot_size") or 1),
                _decimal(record.get("tick_size"), 6),
                bool(record.get("weekly", False)),
                "active",
                today,
                today,
                "upstox",
                raw_id,
                version,
                timestamp,
            ])

        if rows:
            self.client.insert(
                "ref_contracts",
                rows,
                column_names=[
                    "contract_id", "contract_key", "instrument_id", "trading_symbol", "contract_type",
                    "segment", "expiry", "strike_price", "lot_size", "tick_size", "weekly", "status",
                    "first_seen", "last_seen", "source", "raw_id", "version", "ingested_at",
                ],
            )
        return lookup

    def write_candles_1min(
        self,
        candles: pd.DataFrame,
        *,
        instrument_id: uuid.UUID,
        symbol: str,
        contract_id: uuid.UUID | None = None,
        source: str = "upstox",
        raw_id: uuid.UUID | None = None,
        market_code: str = "IND",
    ) -> int:
        """Append a versioned batch of one-minute candles."""
        return self.write_candles_1min_batch(
            [{
                "candles": candles,
                "instrument_id": instrument_id,
                "contract_id": contract_id,
                "symbol": symbol,
                "raw_id": raw_id,
            }],
            source=source,
            market_code=market_code,
        )

    def write_candles_1min_batch(
        self,
        series_batches: Sequence[Mapping[str, Any]],
        *,
        source: str = "upstox",
        market_code: str = "IND",
    ) -> int:
        """Append candles for many instruments with one ClickHouse insert."""

        timestamp = _utc_now()
        version = _version(timestamp)
        rows = []
        for item in series_batches:
            candles = item["candles"]
            if candles.empty:
                continue
            for _, candle in candles.iterrows():
                rows.append([
                    item["instrument_id"],
                    item.get("contract_id") or _NO_CONTRACT_ID,
                    item["symbol"],
                    market_code,
                    _as_utc_datetime(candle["timestamp"]),
                    _decimal(candle.get("open"), 6),
                    _decimal(candle.get("high"), 6),
                    _decimal(candle.get("low"), 6),
                    _decimal(candle.get("close"), 6),
                    _integer(candle.get("volume")),
                    _integer(candle.get("oi")),
                    source,
                    item.get("raw_id"),
                    timestamp,
                    timestamp,
                    version,
                ])
        if not rows:
            return 0
        self.client.insert(
            "market_candles_1min",
            rows,
            column_names=[
                "instrument_id", "contract_id", "symbol", "market_code", "bar_time", "open", "high",
                "low", "close", "volume", "oi", "source", "raw_id", "as_of_time", "ingested_at",
                "version",
            ],
        )
        return len(rows)

    def latest_candle_times(
        self,
        series: Sequence[Mapping[str, Any]],
        *,
        source: str = "upstox",
        before: datetime | None = None,
    ) -> dict[tuple[uuid.UUID, uuid.UUID], datetime]:
        """Return the latest stored candle for the requested series before a cutoff."""

        requested = {
            (
                uuid.UUID(str(item["instrument_id"])),
                uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID)),
            )
            for item in series
        }
        if not requested:
            return {}

        conditions = [
            "market_code = 'IND'",
            "source = {source:String}",
            "instrument_id IN {instrument_ids:Array(UUID)}",
        ]
        parameters: dict[str, Any] = {
            "source": source,
            "instrument_ids": sorted({key[0] for key in requested}, key=str),
        }
        if before is not None:
            conditions.append("bar_time < {before:DateTime64(3, 'UTC')}")
            parameters["before"] = before
        result = self.client.query(
            f"""
            SELECT instrument_id, contract_id, max(bar_time) AS last_bar_time
            FROM market_candles_1min FINAL
            WHERE {' AND '.join(conditions)}
            GROUP BY instrument_id, contract_id
            """,
            parameters=parameters,
        )
        return {
            (row[0], row[1]): row[2]
            for row in result.result_rows
            if (row[0], row[1]) in requested
        }

    def sync_expected_india_series(
        self,
        series: Sequence[Mapping[str, Any]],
        *,
        source: str = "upstox",
        universe: str = "default",
        resolution: str = "1min",
    ) -> int:
        """Replace the active expected-series set used by coverage checks."""

        timestamp = _utc_now()
        version = _version(timestamp)
        current = {
            (
                uuid.UUID(str(item["instrument_id"])),
                uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID)),
            )
            for item in series
        }
        result = self.client.query(
            """
            SELECT instrument_id, contract_id, symbol, universe, resolution
            FROM india_expected_series FINAL
            WHERE source = {source:String} AND active
            """,
            parameters={"source": source},
        )
        existing = {
            (row[0], row[1]): (row[2], row[3], row[4])
            for row in result.result_rows
        }
        rows = [
            [
                instrument_id,
                contract_id,
                str(item["symbol"]),
                source,
                universe,
                resolution,
                True,
                version,
                timestamp,
            ]
            for item in series
            for instrument_id, contract_id in [
                (
                    uuid.UUID(str(item["instrument_id"])),
                    uuid.UUID(str(item.get("contract_id") or _NO_CONTRACT_ID)),
                )
            ]
        ]
        for key, (symbol, old_universe, old_resolution) in existing.items():
            if key not in current:
                rows.append([
                    key[0], key[1], symbol, source, old_universe, old_resolution,
                    False, version, timestamp,
                ])
        if rows:
            self.client.insert(
                "india_expected_series",
                rows,
                column_names=[
                    "instrument_id", "contract_id", "symbol", "source", "universe",
                    "resolution", "active", "version", "ingested_at",
                ],
            )
        return len(current)

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
        """Record the start of an Indian data ingestion run."""

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
        timestamp = _utc_now()
        self.client.insert(
            "ingestion_runs",
            [[
                handle.run_id, handle.market_code, handle.pipeline, handle.source, handle.universe,
                status, handle.started_at, completed_at, handle.requested_series,
                successful_series, failed_series, rows_written, error,
                json.dumps(dict(handle.metadata), sort_keys=True), _version(timestamp), timestamp,
            ]],
            column_names=[
                "run_id", "market_code", "pipeline", "source", "universe", "status",
                "started_at", "completed_at", "requested_series", "successful_series",
                "failed_series", "rows_written", "error", "metadata_json", "version",
                "ingested_at",
            ],
        )


def _decimal(value: Any, scale: int) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))


def _integer(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _decoded_text(value: Any) -> str:
    """Normalize ClickHouse FixedString values returned as padded bytes."""

    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    return str(value)


def _epoch_ms_to_date(value: Any) -> date | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date()


def _as_utc_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").to_pydatetime()


def _open_ssh_tunnel(ssh_host: str, remote_host: str, remote_port: int) -> Any:
    """Open an SSH tunnel to reach a loopback-bound ClickHouse over the VPS.

    Reads CLICKHOUSE_SSH_{USER,PORT,KEY_PATH,PASSWORD} from the environment;
    CLICKHOUSE_SSH_KEY_PATH takes precedence over CLICKHOUSE_SSH_PASSWORD, and
    both fall back to whatever keys the local ssh-agent / default identity
    files provide.
    """
    try:
        import paramiko
        if not hasattr(paramiko, "DSSKey"):
            paramiko.DSSKey = paramiko.RSAKey
        from sshtunnel import SSHTunnelForwarder
    except ImportError as exc:
        raise RuntimeError(
            "sshtunnel is required when CLICKHOUSE_SSH_HOST is set; install "
            "with `pip install -e \".[ops]\"`"
        ) from exc

    ssh_user = os.getenv("CLICKHOUSE_SSH_USER", "ubuntu")
    ssh_port = int(os.getenv("CLICKHOUSE_SSH_PORT", "22"))
    key_path_raw = os.getenv("CLICKHOUSE_SSH_KEY_PATH")
    key_path = str(Path(key_path_raw).expanduser()) if key_path_raw else None
    kwargs: dict[str, Any] = {
        "ssh_username": ssh_user,
        "remote_bind_address": (remote_host, remote_port),
        "local_bind_address": ("127.0.0.1",),
        "set_keepalive": 30.0,
    }
    if key_path:
        kwargs["ssh_pkey"] = key_path
    ssh_password = get_secret("CLICKHOUSE_SSH_PASSWORD", "")
    if ssh_password:
        kwargs["ssh_password"] = ssh_password

    tunnel = SSHTunnelForwarder((ssh_host, ssh_port), **kwargs)
    tunnel.start()
    return tunnel
