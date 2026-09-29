"""Political alt-data dataset contracts (docs/architecture/07 §5.5, P5).

* ``ref.legislators`` - legislators (+ terms), committees, committee memberships.
  Legislators carry two aliases: ``bioguide`` and the ``legislator_name``
  match key (``identifiers.legislator_name_key``), so filings that name a
  filer can be resolved by the DB service instead of by a provider.
* ``alt.political_filings`` - disclosure filing index rows.
* ``alt.political_trades`` - transactions parsed from one filing. The
  provider supplies a ``native_key`` (content hash); the DB service mints
  ``political_trade_id`` from it and resolves the ticker point-in-time.

None of these tables has ``source`` in its sort key, so shadow bindings only
normalize and count (07 §9.2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

from factorlab.ingest.datasets.common import EntityRef, InstrumentRef, WriteResult
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.provider import Provenance, ProviderStorage

Chamber = Literal["house", "senate"]
CommitteeRole = Literal["chair", "vice_chair", "ranking_member", "member"]
TransactionType = Literal["purchase", "sale_full", "sale_partial", "exchange"]


def congress_number(day: date) -> int:
    """The Congress in session on ``day`` (the 1st convened in 1789, two-year terms)."""
    return (day.year - 1789) // 2 + 1


@dataclass(frozen=True, slots=True)
class LegislatorTerm:
    chamber: Chamber
    state: str
    start: date
    end: date
    party: str = ""
    district: int | None = None
    seat_class: int | None = None


@dataclass(frozen=True, slots=True)
class LegislatorRecord:
    entity: EntityRef  # alias_kind='bioguide'
    first_name: str
    last_name: str
    terms: tuple[LegislatorTerm, ...]

    def __post_init__(self) -> None:
        if self.entity.alias_kind != "bioguide":
            raise ValueError("legislators are keyed by bioguide id")
        if not self.terms:
            raise ValueError(f"{self.entity.alias_value}: a legislator needs at least one term")


@dataclass(frozen=True, slots=True)
class CommitteeRecord:
    committee_code: str
    name: str
    chamber: str
    congress: int
    parent_code: str | None = None
    jurisdiction: str = ""
    url: str = ""

    @property
    def is_subcommittee(self) -> bool:
        return self.parent_code is not None


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    committee_code: str
    legislator: EntityRef  # alias_kind='bioguide'
    role: CommitteeRole
    congress: int
    party_side: str = ""  # 'majority' / 'minority' as reported
    rank: int = 0


@dataclass(frozen=True, slots=True)
class PoliticalFilingRecord:
    filing_id: str
    chamber: Chamber
    filing_type: str
    filing_year: int
    filing_date: date
    filer_name_raw: str
    filing_url: str
    filer: EntityRef | None = None  # usually alias_kind='legislator_name'
    country_code: str = "US"


@dataclass(frozen=True, slots=True)
class PoliticalTradeRecord:
    native_key: str
    filing_id: str
    chamber: Chamber
    transaction_date: date
    asset_name_raw: str
    asset_type_code: str
    transaction_type: TransactionType
    ticker_raw: str | None = None
    instrument: InstrumentRef | None = None  # canonical ticker, country-scoped
    notification_date: date | None = None
    owner_code: str = ""
    filer_type: str = "self"
    amount_min: int | None = None
    amount_max: int | None = None
    amount_str: str = ""
    parser_version: str = ""
    country_code: str = "US"

    def __post_init__(self) -> None:
        if not self.native_key:
            raise ValueError("a political trade needs a source-native key")


@dataclass(frozen=True, slots=True)
class FilingRef:
    filing_id: str
    filing_year: int
    filing_url: str


@dataclass(frozen=True, slots=True)
class FilingsRequest:
    """Which filings to fetch documents for; built from ``PoliticalReader``."""

    market: str
    filings: tuple[FilingRef, ...]
    params: Mapping[str, Any] = field(default_factory=dict, hash=False)


LegislatorRow = LegislatorRecord | CommitteeRecord | MembershipRecord


@runtime_checkable
class LegislatorSink(ProviderStorage, Protocol):
    def write_legislators(
        self,
        rows: Sequence[LegislatorRow],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult: ...


@runtime_checkable
class PoliticalFilingSink(ProviderStorage, Protocol):
    def write_political_filings(
        self, rows: Sequence[PoliticalFilingRecord], *, provenance: Provenance
    ) -> WriteResult: ...


@runtime_checkable
class PoliticalTradeSink(ProviderStorage, Protocol):
    def write_political_trades(
        self, rows: Sequence[PoliticalTradeRecord], *, provenance: Provenance
    ) -> WriteResult: ...


@runtime_checkable
class PoliticalReader(Protocol):
    def recent_filings(
        self, *, chamber: str, limit: int, country_code: str = "US"
    ) -> list[FilingRef]:
        """Newest filings by ``filing_date`` (the documents a trades run should fetch)."""
        ...


__all__ = [
    "Chamber",
    "CommitteeRecord",
    "CommitteeRole",
    "FilingRef",
    "FilingsRequest",
    "LegislatorRecord",
    "LegislatorRow",
    "LegislatorSink",
    "LegislatorTerm",
    "MembershipRecord",
    "PoliticalFilingRecord",
    "PoliticalFilingSink",
    "PoliticalReader",
    "PoliticalTradeRecord",
    "PoliticalTradeSink",
    "TransactionType",
    "congress_number",
]
