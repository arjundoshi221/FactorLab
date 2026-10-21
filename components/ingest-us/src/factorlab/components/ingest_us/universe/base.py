"""Base class implementing atomic universe resolution semantics."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from factorlab.components.ingest_us.universe.models import (
    ResolvedConstituent,
    ResolvedUniverse,
    UniverseRequest,
    normalize_symbol,
)


class EquityValidator(Protocol):
    def validate(self, symbols: Iterable[str]) -> list[ResolvedConstituent]: ...


class UniverseResolver(ABC):
    """Resolve configured indexes, then validate the complete union atomically."""

    provider: str

    def __init__(self, *, validator: EquityValidator):
        self.validator = validator
        self._provenance: dict[str, object] = {}

    @abstractmethod
    def _retrieve_index(self, name: str) -> Iterable[str]:
        """Return raw symbols for one logical index."""

    def resolve(self, request: UniverseRequest) -> ResolvedUniverse:
        requested = set(request.explicit_symbols)
        counts = {}
        self._provenance = {}
        for index in request.indexes:
            symbols = {normalize_symbol(value) for value in self._retrieve_index(index.name)}
            if len(symbols) < index.minimum_constituents:
                raise ValueError(
                    f"{index.name} returned {len(symbols)} constituents; "
                    f"minimum is {index.minimum_constituents}"
                )
            counts[index.name] = len(symbols)
            requested.update(symbols)
        if not requested:
            raise ValueError("configured universe resolved to no symbols")

        validated = self.validator.validate(sorted(requested))
        by_symbol = {item.symbol: item for item in validated}
        unresolved = sorted(requested - by_symbol.keys())
        # A holdings proxy may drop a few members Schwab cannot validate; explicitly
        # configured symbols and anything beyond the allowance still reject the roster.
        explicit = set(request.explicit_symbols)
        if unresolved and (
            len(unresolved) > request.max_unvalidated or explicit.intersection(unresolved)
        ):
            preview = ", ".join(unresolved[:10])
            raise ValueError(
                f"Schwab could not validate {len(unresolved)} configured equities: "
                f"{preview}{'...' if len(unresolved) > 10 else ''}"
            )
        provenance: dict[str, object] = {"index_counts": counts, **self._provenance}
        if unresolved:
            provenance["unvalidated"] = unresolved
        return ResolvedUniverse(
            provider=self.provider,
            name=request.name,
            constituents=tuple(
                by_symbol[symbol] for symbol in sorted(by_symbol.keys() & requested)
            ),
            resolved_at=datetime.now(UTC),
            provenance=provenance,
        )
