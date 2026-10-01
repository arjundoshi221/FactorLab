"""Provider-neutral universe requests and resolved constituents."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,13}$")


def normalize_symbol(value: object) -> str:
    """Canonicalize US class-share separators to a dash."""
    symbol = str(value or "").strip().upper().removesuffix(".US")
    symbol = symbol.replace(".", "-").replace("/", "-")
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"Unsupported US symbol: {value!r}")
    return symbol


class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    minimum_constituents: int = Field(ge=1)


class UniverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    indexes: tuple[IndexRequest, ...] = ()
    explicit_symbols: tuple[str, ...] = ()

    @field_validator("explicit_symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, values):
        return tuple(sorted({normalize_symbol(value) for value in (values or [])}))

    @model_validator(mode="after")
    def validate_request(self):
        names = [item.name for item in self.indexes]
        if len(names) != len(set(names)):
            raise ValueError("index names must be unique")
        if not names and not self.explicit_symbols:
            raise ValueError("at least one index or explicit symbol is required")
        return self


class ResolvedConstituent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    name: str
    exchange: str
    currency: str
    instrument_type: str

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value):
        return normalize_symbol(value)


class ResolvedUniverse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    name: str
    constituents: tuple[ResolvedConstituent, ...]
    resolved_at: datetime
    provenance: dict[str, Any]
