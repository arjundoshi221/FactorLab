"""Version-two universe configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from factorlab.components.ingest_us.settings import ConstituentCsvProvider
from factorlab.components.ingest_us.universe.models import (
    IndexRequest,
    UniverseRequest,
    normalize_symbol,
)


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


class IsharesIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    # A relayed copy of ``url`` under the read-only constituents mount, for hosts the
    # iShares CDN refuses (deploy/ishares-relay.ps1).
    path: str = Field(default="", pattern=r"^(/app/config/constituents/[A-Za-z0-9._-]+\.csv)?$")
    name: str = ""
    max_age_days: int = Field(default=7, ge=1)


class IsharesProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indexes: dict[str, IsharesIndex]


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
    constituent_csv: ConstituentCsvProvider | None = None
    ishares: IsharesProvider | None = None


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
    # Index members Schwab may fail to validate before the roster is rejected. Only for
    # holdings proxies (ishares); exact rosters and extra_symbols must validate fully.
    max_unvalidated: int = Field(default=0, ge=0)
    # Publish GICS sectors from this provider's holdings files with the roster.
    sector_provider: Literal["ishares"] | None = None
    # Snapshot shares outstanding for every published equity after each refresh.
    shares_outstanding: bool = False

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
        if self.provider not in {"github_csv", "eodhd", "constituent_csv", "ishares"}:
            raise ValueError(f"unknown universe provider: {self.provider}")
        provider = getattr(self.providers, self.provider)
        if provider is None:
            raise ValueError(f"provider mapping is missing for {self.provider}")
        missing = sorted(set(names) - set(provider.indexes))
        if missing:
            raise ValueError(
                f"provider {self.provider} has no mapping for indexes: {', '.join(missing)}"
            )
        if self.max_unvalidated and self.provider != "ishares":
            raise ValueError("max_unvalidated applies only to holdings proxies (ishares)")
        if self.sector_provider:
            sectors = getattr(self.providers, self.sector_provider)
            if sectors is None or set(names) - set(sectors.indexes):
                raise ValueError(f"sector_provider {self.sector_provider} must map every index")
        return self

    def request(self) -> UniverseRequest:
        return UniverseRequest(
            name=self.name,
            indexes=tuple(IndexRequest(**item.model_dump()) for item in self.indexes),
            explicit_symbols=tuple(self.extra_symbols),
            max_unvalidated=self.max_unvalidated,
        )


def load_config(path: str | Path) -> UniverseConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("universe config must be a YAML mapping")
    return UniverseConfig.model_validate(payload)
