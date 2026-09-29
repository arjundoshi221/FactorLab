"""ClickHouse connection, run-lifecycle base, and value helpers shared by the v2 writers."""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import clickhouse_connect
import pandas as pd

from factorlab.core.secrets import get_secret

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


def rows(result: Any) -> list[dict[str, Any]]:
    """Query result -> row dicts, with naive ClickHouse datetimes marked as UTC."""
    return [{key: value.replace(tzinfo=timezone.utc)
             if isinstance(value, datetime) and value.tzinfo is None else value
             for key, value in zip(result.column_names, row, strict=True)}
            for row in result.result_rows]


class ClickHouseStorage:
    """ClickHouse connection plus the ingestion-run lifecycle the v2 writers extend."""

    def __init__(self, client: Any, tunnel: Any = None) -> None:
        self.client = client
        self._tunnel = tunnel

    @classmethod
    def from_environment(cls, **client_options: Any) -> "ClickHouseStorage":
        """Connect from environment settings; ``client_options`` pass through to clickhouse_connect."""
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
                **client_options,
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
