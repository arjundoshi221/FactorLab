"""Version-two universe configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from factorlab.universe.models import IndexRequest, UniverseRequest, normalize_symbol


class ConfiguredIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    minimum_constituents: int = Field(ge=1)


class GithubCsvIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    symbol_column: str = Field(min_length=1)


class EodhdIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.-]{0,30}\.INDX$")

    @field_validator("symbol", mode="before")
    @classmethod
    def uppercase(cls, value):
        return str(value).strip().upper()


class GithubCsvProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indexes: dict[str, GithubCsvIndex]


class EodhdProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indexes: dict[str, EodhdIndex]


class ProviderConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    github_csv: GithubCsvProvider | None = None
    eodhd: EodhdProvider | None = None


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    provider: str = Field(min_length=1)
    daily_source: Literal["schwab"]
    refresh_interval_minutes: int = Field(ge=1)
    indexes: list[ConfiguredIndex] = Field(default_factory=list)
    providers: ProviderConfigs
    extra_symbols: list[str] = Field(default_factory=list)

    @field_validator("extra_symbols")
    @classmethod
    def normalize_extras(cls, values):
        return sorted({normalize_symbol(value) for value in values})

    @model_validator(mode="after")
    def validate_mappings(self):
        names = [item.name for item in self.indexes]
        if len(names) != len(set(names)):
            raise ValueError("index names must be unique")
        if not names and not self.extra_symbols:
            raise ValueError("at least one index or extra symbol is required")
        if self.provider not in {"github_csv", "eodhd"}:
            raise ValueError(f"unknown universe provider: {self.provider}")
        provider = getattr(self.providers, self.provider)
        if provider is None:
            raise ValueError(f"provider mapping is missing for {self.provider}")
        missing = sorted(set(names) - set(provider.indexes))
        if missing:
            raise ValueError(
                f"provider {self.provider} has no mapping for indexes: {', '.join(missing)}"
            )
        return self

    def request(self) -> UniverseRequest:
        return UniverseRequest(
            name=self.name,
            indexes=tuple(IndexRequest(**item.model_dump()) for item in self.indexes),
            explicit_symbols=tuple(self.extra_symbols),
        )


def load_config(path: str | Path) -> UniverseConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("universe config must be a YAML mapping")
    return UniverseConfig.model_validate(payload)
