"""GitHub CSV universe source: ``plan -> fetch -> normalize`` (docs/architecture/07 §6)."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, ConfigDict, Field

from factorlab.ingest.bindings import ProviderSettings
from factorlab.ingest.datasets import (
    Capabilities,
    ConstituentRecord,
    FetchUnit,
    InstrumentRef,
    ReferenceRequest,
)
from factorlab.ingest.errors import NormalizationError, PermanentError, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.transport import raise_for_status

ALLOWED_HOSTS = frozenset({"github.com", "raw.githubusercontent.com"})
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,13}$")


class CsvIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    symbol_column: str = "Symbol"
    minimum_constituents: int = Field(ge=1)
    name: str = ""
    country: str = "US"


class GithubCsvSettings(ProviderSettings):
    indexes: dict[str, CsvIndex] = Field(default_factory=dict)
    timeout: float = 30.0


def validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise PermanentError("GitHub CSV URLs must use HTTPS on an approved GitHub host")


def normalize_symbol(value: str) -> str | None:
    """``BRK.B`` / ``BRK/B`` -> ``BRK-B``; matches ``universe.models.normalize_symbol``."""
    symbol = value.strip().upper().removesuffix(".US").replace(".", "-").replace("/", "-")
    return symbol if _SYMBOL.fullmatch(symbol) else None


class GithubCsvUniverse:
    provider: ClassVar[str] = "github_csv"
    dataset: ClassVar[str] = "ref.universe_membership"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=frozenset({"USA"}))
    settings_model: ClassVar[type[GithubCsvSettings]] = GithubCsvSettings

    def __init__(
        self,
        settings: GithubCsvSettings,
        *,
        instance: str = "github_csv",
        session: requests.Session | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.instance = instance
        self.session = session or requests.Session()
        self._clock = clock

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        codes = request.params.get("universes") or sorted(self.settings.indexes)
        unknown = sorted(set(codes) - set(self.settings.indexes))
        if unknown:
            raise ValueError(f"no github_csv index configured for {unknown}")
        return [
            FetchUnit(f"universe:{code}", f"{self.instance}:csv", params={"universe": code})
            for code in codes
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        code = str(unit.params["universe"])
        index = self.settings.indexes[code]
        validate_url(index.url)
        try:
            response = self.session.get(
                index.url, timeout=self.settings.timeout, allow_redirects=True
            )
        except requests.RequestException as exc:
            raise TransientError(f"GitHub CSV request failed: {type(exc).__name__}") from exc
        validate_url(response.url)  # a redirect must stay on GitHub
        raise_for_status("GitHub", response.status_code, response.headers, unit.name)
        return RawCapture(
            body=response.content,
            request_key=unit.name,
            transport="http",
            fetched_at=self._clock(),
            source_url=response.url,
            content_type=response.headers.get("Content-Type", "text/csv"),
            status_code=response.status_code,
            headers=dict(response.headers),
            metadata={"universe": code, **index.model_dump()},
        )

    def normalize(self, capture: RawCapture) -> Sequence[ConstituentRecord]:
        meta = capture.metadata
        code, column = str(meta["universe"]), str(meta["symbol_column"])
        try:
            reader = csv.DictReader(io.StringIO(capture.body.decode("utf-8-sig")))
        except UnicodeDecodeError as exc:
            raise NormalizationError(f"{code}: CSV is not UTF-8") from exc
        if not reader.fieldnames or column not in reader.fieldnames:
            raise NormalizationError(f"{code}: CSV has no {column!r} column")
        symbols: set[str] = set()
        for row in reader:
            raw = str(row.get(column) or "").strip()
            symbol = normalize_symbol(raw) if raw else None
            if symbol is None:
                raise NormalizationError(f"{code}: unusable symbol {raw!r}")
            symbols.add(symbol)
        minimum = int(meta["minimum_constituents"])
        if len(symbols) < minimum:
            raise NormalizationError(
                f"{code}: {len(symbols)} constituents is below the minimum of {minimum}"
            )
        country = str(meta.get("country") or "US")
        return [
            ConstituentRecord(
                code,
                InstrumentRef("", "", "", symbol, country),
                universe_name=str(meta.get("name") or ""),
            )
            for symbol in sorted(symbols)
        ]


__all__ = ["GithubCsvSettings", "GithubCsvUniverse", "normalize_symbol", "validate_url"]
