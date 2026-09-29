"""Typed settings for every FactorLab library and component.

Subclass :class:`FactorLabSettings` and give each field the environment name
operators already use, preferably as ``env_prefix`` + field name (``CLICKHOUSE_`` +
``host``; matching is case-insensitive). Use ``validation_alias`` only for a field
whose variable breaks that pattern. Never enable ``populate_by_name``: it makes the
environment source also accept the bare field name, so a stray ``USERNAME`` or
``HOST`` variable would silently configure the field. Empty variables count as unset.

A field marked ``Secret("NAME")`` is resolved through
:func:`factorlab.core.secrets.get_secret` (``NAME_FILE``, then
``FACTORLAB_SECRETS_DIR/NAME``, then the ``NAME`` environment variable), so
production reads it from the runtime secret volume and development from the
environment.

Precedence: explicit keyword arguments, then secrets, then environment
variables, then defaults. ``.env`` files are never read here; for local runs
use ``uv run --env-file .env ...``.

Rotating credentials (the Upstox daily token, the Schwab access token) must not
be frozen into a settings object: keep only the secret's *name* in settings and
call ``get_secret`` per request.
"""

from __future__ import annotations

from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from factorlab.core.secrets import get_secret


class Secret:
    """Field metadata: read this field through ``get_secret(name)``."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Secret({self.name!r})"


class _SecretSource(PydanticBaseSettingsSource):
    """Settings source for fields annotated with :class:`Secret`."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        marker = next((m for m in field.metadata if isinstance(m, Secret)), None)
        return (get_secret(marker.name) if marker else None), field_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, field in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field, name)
            if value is not None:
                values[key] = value
        return values


class FactorLabSettings(BaseSettings):
    """Base class: immutable; ignores unrelated and empty environment variables."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore", env_ignore_empty=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, _SecretSource(settings_cls), env_settings)


__all__ = ["FactorLabSettings", "Secret"]
