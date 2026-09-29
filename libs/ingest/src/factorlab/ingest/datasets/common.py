"""Provider-neutral building blocks shared by every dataset contract (docs/architecture/07 §5-6).

Nothing in this module knows about a vendor. Records reference instruments and
entities through :class:`InstrumentRef` / :class:`EntityRef`; canonical UUIDs
are minted only by the DB service (rule R5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Protocol, TypeVar, runtime_checkable

from factorlab.ingest.provider import RawCapture

RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
RecordT_co = TypeVar("RecordT_co", covariant=True)

# docs/architecture/06 vocabulary.
RESOLUTIONS = frozenset({"5s", "1min", "5min", "15min", "1h", "daily", "weekly", "monthly"})
SESSIONS = frozenset({"regular", "pre", "post", "open_auction", "close_auction"})


def require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime, got {value!r}")


def require_decimal(name: str, value: object, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal, got {value!r}")


def require_int(name: str, value: object, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got {value!r}")


@dataclass(frozen=True, slots=True)
class InstrumentRef:
    """How a provider names an instrument, plus the natural keys the DB resolves on.

    ``alias_kind``/``alias_value`` is the provider's own identifier. It may be
    empty only for hints (a futures underlying known by symbol, an index
    constituent list of bare tickers), in which case the DB service resolves on
    ``trading_symbol`` within ``exchange_code`` or, when the exchange is blank,
    within ``country_code`` (only if exactly one active listing matches).
    """

    alias_kind: str
    alias_value: str
    exchange_code: str
    trading_symbol: str
    country_code: str
    isin: str | None = None
    cusip: str | None = None
    figi: str | None = None

    def __post_init__(self) -> None:
        if bool(self.alias_kind) != bool(self.alias_value):
            raise ValueError("alias_kind and alias_value must be given together")
        if not self.alias_value and not self.trading_symbol:
            raise ValueError("an InstrumentRef needs an alias or a trading_symbol")
        if len(self.country_code) != 2 or not self.country_code.isupper():
            raise ValueError(f"country_code must be ISO-3166 alpha-2, got {self.country_code!r}")
        if self.isin is not None and (len(self.isin) != 12 or not self.isin.isalnum()):
            raise ValueError(f"isin must be 12 alphanumeric characters, got {self.isin!r}")

    @property
    def has_alias(self) -> bool:
        return bool(self.alias_value)

    def label(self) -> str:
        return (
            self.alias_value or f"{self.exchange_code or self.country_code}:{self.trading_symbol}"
        )


@dataclass(frozen=True, slots=True)
class EntityRef:
    """A person or organisation reference (legislator, committee, issuer)."""

    alias_kind: str
    alias_value: str
    entity_type: str
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.alias_kind or not self.alias_value:
            raise ValueError("EntityRef needs alias_kind and alias_value")


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a source adapter can serve; bindings are validated against it."""

    markets: frozenset[str]
    resolutions: frozenset[str] = frozenset()
    alias_kind: str = ""
    max_lookback: timedelta | None = None
    max_batch: int = 1
    polling: bool = False

    def __post_init__(self) -> None:
        if not self.markets:
            raise ValueError("Capabilities.markets must not be empty")
        unknown = set(self.resolutions) - RESOLUTIONS
        if unknown:
            raise ValueError(f"unknown resolutions: {sorted(unknown)}")
        if self.max_batch < 1:
            raise ValueError("max_batch must be positive")


@dataclass(frozen=True, slots=True)
class FetchUnit:
    """One fetch-and-write step; becomes one ``RunContext`` unit outcome.

    ``params`` is provider-private. ``instruments`` and ``start``/``end`` are
    declared so the engine and the conformance suite can check batch and
    lookback limits without understanding ``params``.
    """

    name: str
    source_channel: str
    params: Mapping[str, Any] = field(default_factory=dict, hash=False)
    instruments: tuple[InstrumentRef, ...] = ()
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.source_channel:
            raise ValueError("FetchUnit needs a name and a source_channel")
        for name in ("start", "end"):
            value = getattr(self, name)
            if value is not None:
                require_utc(name, value)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What a sink reports back for one write call."""

    rows_written: int
    unresolved: int = 0
    resolved: int = 0  # identities found; the only signal in resolve_only mode


@runtime_checkable
class DatasetSource(Protocol[RequestT_contra, RecordT_co]):
    """A provider's implementation of one dataset: ``plan -> fetch -> normalize``.

    * ``plan`` is pure: split a request into units sized to vendor limits.
    * ``fetch`` is impure: transport, auth, retries; returns one raw capture.
    * ``normalize`` is pure: captured bytes (and capture metadata) -> records.
      It never touches a live client, the network, or the clock (rule R7).
    """

    provider: ClassVar[str]
    dataset: ClassVar[str]
    capabilities: ClassVar[Capabilities]

    def plan(self, request: RequestT_contra) -> Sequence[FetchUnit]: ...

    def fetch(self, unit: FetchUnit) -> RawCapture: ...

    def normalize(self, capture: RawCapture) -> Sequence[RecordT_co]: ...


__all__ = [
    "RESOLUTIONS",
    "SESSIONS",
    "Capabilities",
    "DatasetSource",
    "EntityRef",
    "FetchUnit",
    "InstrumentRef",
    "WriteResult",
    "require_decimal",
    "require_int",
    "require_utc",
]
