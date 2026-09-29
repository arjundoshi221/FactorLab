"""Typed settings: secret resolution, environment names, and ClickHouse connection wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from factorlab.clickhouse import ClickHouse, ClickHouseSettings
from factorlab.core.settings import FactorLabSettings, Secret


class _Example(FactorLabSettings):
    model_config = SettingsConfigDict(env_prefix="EXAMPLE_")

    region: str = "us"
    token: Annotated[SecretStr | None, Secret("EXAMPLE_TOKEN")] = None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (
        "EXAMPLE_REGION",
        "EXAMPLE_TOKEN",
        "EXAMPLE_TOKEN_FILE",
        "FACTORLAB_SECRETS_DIR",
        "CLICKHOUSE_HOST",
        "CLICKHOUSE_PORT",
        "CLICKHOUSE_PASSWORD",
        "CLICKHOUSE_SSH_HOST",
        "CLICKHOUSE_SSH_KEY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_environment_names_and_defaults(monkeypatch):
    assert _Example().region == "us"
    monkeypatch.setenv("EXAMPLE_REGION", "in")
    assert _Example().region == "in"


def test_secret_file_wins_over_environment(monkeypatch, tmp_path):
    (tmp_path / "EXAMPLE_TOKEN").write_text("from-volume\n", encoding="utf-8")
    monkeypatch.setenv("EXAMPLE_TOKEN", "from-env")
    monkeypatch.setenv("FACTORLAB_SECRETS_DIR", str(tmp_path))
    assert _Example().token.get_secret_value() == "from-volume"


def test_secret_falls_back_to_environment_and_is_masked(monkeypatch):
    monkeypatch.setenv("EXAMPLE_TOKEN", "from-env")
    settings = _Example()
    assert settings.token.get_secret_value() == "from-env"
    assert "from-env" not in repr(settings)
    assert "from-env" not in settings.model_dump_json()


def test_keyword_arguments_override_everything(monkeypatch):
    monkeypatch.setenv("EXAMPLE_REGION", "in")
    assert _Example(region="eu").region == "eu"


def test_bare_field_names_are_not_read_from_the_environment(monkeypatch):
    # Windows sets USERNAME; containers commonly carry HOST/PORT. Only CLICKHOUSE_* count.
    monkeypatch.setenv("USERNAME", "someone")
    monkeypatch.setenv("HOST", "elsewhere")
    monkeypatch.setenv("REGION", "jp")
    assert (ClickHouseSettings().username, ClickHouseSettings().host) == ("factorlab", "localhost")
    assert _Example().region == "us"


def test_empty_variables_count_as_unset(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_SSH_HOST", "")
    monkeypatch.setenv("CLICKHOUSE_SSH_KEY_PATH", "")
    settings = ClickHouseSettings()
    assert settings.ssh_host is None and settings.ssh_key_path is None


def test_settings_are_immutable():
    with pytest.raises(ValidationError):
        _Example().region = "jp"


def test_clickhouse_settings_read_the_deploy_bundle_names(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_HOST", "clickhouse")
    monkeypatch.setenv("CLICKHOUSE_PORT", "9123")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "s3cret")
    settings = ClickHouseSettings()
    assert (settings.host, settings.port) == ("clickhouse", 9123)
    assert settings.password.get_secret_value() == "s3cret"
    assert settings.ssh_host is None


def test_connect_passes_settings_to_the_client(monkeypatch):
    calls = {}

    def get_client(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr("factorlab.clickhouse.connection.clickhouse_connect.get_client", get_client)
    settings = ClickHouseSettings(host="db", port=8124, password="pw", database="default")
    with ClickHouse.connect(settings, connect_timeout=5) as connection:
        assert connection.client is not None
    assert calls == {
        "host": "db",
        "port": 8124,
        "username": "factorlab",
        "password": "pw",
        "database": "default",
        "connect_timeout": 5,
    }


def test_connect_uses_a_tunnel_only_when_configured(monkeypatch):
    opened = []

    class Tunnel:
        local_bind_port = 40123

        def stop(self):
            opened.append("stopped")

    monkeypatch.setattr(
        "factorlab.clickhouse.connection.open_ssh_tunnel",
        lambda s: opened.append(s.ssh_host) or Tunnel(),
    )
    monkeypatch.setattr(
        "factorlab.clickhouse.connection.clickhouse_connect.get_client",
        lambda **kw: SimpleNamespace(close=lambda: None, **kw),
    )
    connection = ClickHouse.connect(ClickHouseSettings(ssh_host="vps"))
    assert (connection.client.host, connection.client.port) == ("127.0.0.1", 40123)
    connection.close()
    assert opened == ["vps", "stopped"]
