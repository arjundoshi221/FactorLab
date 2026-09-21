"""Configuration and resolution of the active EODHD US equity universe."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from factorlab.countries.us.equities.eodhd.us_universe import canonical_symbol, normalize_master

_INDEX_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,30}\.INDX$")


class IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    minimum_constituents: int = Field(ge=1)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        symbol = str(value).strip().upper()
        if not _INDEX_SYMBOL.fullmatch(symbol):
            raise ValueError("index symbols must use EODHD's CODE.INDX form")
        return symbol


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    provider: Literal["eodhd"]
    refresh_interval_minutes: int = Field(ge=1)
    indexes: list[IndexConfig] = Field(default_factory=list)
    extra_symbols: list[str] = Field(default_factory=list)

    @field_validator("extra_symbols")
    @classmethod
    def normalize_extra_symbols(cls, values: list[str]) -> list[str]:
        return sorted({canonical_symbol(value) for value in values})

    @model_validator(mode="after")
    def unique_indexes(self):
        symbols = [item.symbol for item in self.indexes]
        if len(symbols) != len(set(symbols)):
            raise ValueError("index symbols must be unique")
        if not symbols and not self.extra_symbols:
            raise ValueError("at least one index or extra symbol is required")
        return self


def load_config(path: str | Path) -> UniverseConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("universe config must be a YAML mapping")
    return UniverseConfig.model_validate(payload)


def component_symbols(payload: object, index: IndexConfig) -> set[str]:
    """Validate one Components response and return canonical constituents."""
    if isinstance(payload, dict) and "Components" in payload:
        payload = payload["Components"]
    if isinstance(payload, dict):
        records = list(payload.values())
    elif isinstance(payload, list):
        records = payload
    else:
        raise TypeError(f"{index.symbol} returned a malformed Components response")
    if not records:
        raise ValueError(f"{index.symbol} returned no constituents")

    symbols = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("Code"):
            raise ValueError(f"{index.symbol} returned a malformed constituent")
        symbols.add(canonical_symbol(record["Code"]))
    if len(symbols) < index.minimum_constituents:
        raise ValueError(
            f"{index.symbol} returned {len(symbols)} constituents; "
            f"minimum is {index.minimum_constituents}"
        )
    return symbols


def resolve_universe(config: UniverseConfig, client) -> tuple[list[dict], list[dict]]:
    """Fetch and validate the full master and configured union without publishing."""
    raw_master = client.get_exchange_symbols("US", instrument_type="common_stock")
    if not isinstance(raw_master, list) or any(not isinstance(item, dict) for item in raw_master):
        raise ValueError("US common-stock master response is malformed")
    master = normalize_master(raw_master)
    if not master:
        raise ValueError("US common-stock master is empty")
    by_symbol = {item["symbol"]: item for item in master}

    requested = set(config.extra_symbols)
    for index in config.indexes:
        requested.update(component_symbols(client.get_index_components(index.symbol), index))
    if not requested:
        raise ValueError("configured universe resolved to no symbols")

    unresolved = sorted(requested - by_symbol.keys())
    if unresolved:
        preview = ", ".join(unresolved[:10])
        suffix = "..." if len(unresolved) > 10 else ""
        raise ValueError(
            f"{len(unresolved)} configured symbols are not active USD US common stocks: "
            f"{preview}{suffix}"
        )
    return master, [by_symbol[symbol] for symbol in sorted(requested)]
