"""ClickHouse connection settings (environment names unchanged from the deploy bundle)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from factorlab.core.settings import FactorLabSettings, Secret


class ClickHouseSettings(FactorLabSettings):
    """Where and how to reach ClickHouse (``CLICKHOUSE_HOST``, ``CLICKHOUSE_PORT``, ...).

    ``CLICKHOUSE_SSH_HOST`` opens an SSH tunnel to a loopback-bound server (the VPS);
    that needs the ``ssh`` extra (paramiko, sshtunnel).
    """

    model_config = SettingsConfigDict(env_prefix="CLICKHOUSE_")

    host: str = "localhost"
    port: int = 8123
    username: str = "factorlab"
    database: str = "factorlab"
    # The development default matches docker-compose.yml; production reads the secret volume.
    password: Annotated[SecretStr, Secret("CLICKHOUSE_PASSWORD")] = SecretStr("factorlab_dev")

    ssh_host: str | None = None
    ssh_user: str = "ubuntu"
    ssh_port: int = 22
    ssh_key_path: Path | None = None
    ssh_password: Annotated[SecretStr | None, Secret("CLICKHOUSE_SSH_PASSWORD")] = None


__all__ = ["ClickHouseSettings"]
