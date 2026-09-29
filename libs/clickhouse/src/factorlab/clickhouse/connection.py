"""A ClickHouse client with the optional SSH tunnel it was opened through."""

from __future__ import annotations

from typing import Any, Self

import clickhouse_connect

from factorlab.clickhouse.settings import ClickHouseSettings


class ClickHouse:
    """Owns one ``clickhouse_connect`` client (and its SSH tunnel, if any).

    Readers such as the API and the schema tools use it directly; the v2
    writers in ``factorlab.storage`` extend it.
    """

    def __init__(self, client: Any, tunnel: Any = None) -> None:
        self.client = client
        self._tunnel = tunnel

    @classmethod
    def connect(cls, settings: ClickHouseSettings | None = None, **client_options: Any) -> Self:
        """Open a client from *settings* (default: the environment).

        ``client_options`` pass through to ``clickhouse_connect.get_client``.
        """
        settings = settings or ClickHouseSettings()
        tunnel = None
        host, port = settings.host, settings.port
        if settings.ssh_host:
            tunnel = open_ssh_tunnel(settings)
            host, port = "127.0.0.1", tunnel.local_bind_port
        try:
            client = clickhouse_connect.get_client(
                host=host,
                port=port,
                username=settings.username,
                password=settings.password.get_secret_value(),
                database=settings.database,
                **client_options,
            )
        except Exception:
            if tunnel is not None:
                tunnel.stop()
            raise
        return cls(client, tunnel=tunnel)

    @classmethod
    def from_environment(cls, **client_options: Any) -> Self:
        """Connect using settings read from the environment and secret volume."""
        return cls.connect(**client_options)

    def close(self) -> None:
        """Close the client and any SSH tunnel opened for it."""
        try:
            self.client.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
        if self._tunnel is not None:
            try:
                self._tunnel.stop()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
            self._tunnel = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def open_ssh_tunnel(settings: ClickHouseSettings) -> Any:
    """Tunnel to a loopback-bound ClickHouse over SSH (needs the ``ssh`` extra).

    ``CLICKHOUSE_SSH_KEY_PATH`` takes precedence over ``CLICKHOUSE_SSH_PASSWORD``;
    both fall back to the local ssh-agent and default identity files.
    """
    try:
        import paramiko

        if not hasattr(paramiko, "DSSKey"):
            paramiko.DSSKey = paramiko.RSAKey
        from sshtunnel import SSHTunnelForwarder
    except ImportError as exc:
        raise RuntimeError(
            "sshtunnel is required when CLICKHOUSE_SSH_HOST is set; install the ssh extra"
        ) from exc

    kwargs: dict[str, Any] = {
        "ssh_username": settings.ssh_user,
        "remote_bind_address": (settings.host, settings.port),
        "local_bind_address": ("127.0.0.1",),
        "set_keepalive": 30.0,
    }
    if settings.ssh_key_path:
        kwargs["ssh_pkey"] = str(settings.ssh_key_path.expanduser())
    if settings.ssh_password and settings.ssh_password.get_secret_value():
        kwargs["ssh_password"] = settings.ssh_password.get_secret_value()
    tunnel = SSHTunnelForwarder((settings.ssh_host, settings.ssh_port), **kwargs)
    tunnel.start()
    return tunnel


__all__ = ["ClickHouse", "open_ssh_tunnel"]
