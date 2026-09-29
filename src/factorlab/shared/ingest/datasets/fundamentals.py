"""``fundamentals.filings`` dataset contract: regulatory filings + XBRL line items (07 §5.5, P8).

Records name the issuer by an ``EntityRef`` (``alias_kind='cik'`` for SEC) and may
carry a ticker hint; the DB service resolves the issuer entity and mints
``filing_id`` from the accession number (rule R5). Values are reported, not
standardised: ``tag`` is the filer's XBRL concept and ``unit`` the XBRL unit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from factorlab.shared.ingest.datasets.common import (
    EntityRef,
    InstrumentRef,
    WriteResult,
    require_decimal,
)
from factorlab.shared.ingest.provider import Provenance, ProviderStorage

PeriodType = Literal["annual", "quarterly", "other"]


@dataclass(frozen=True, slots=True)
class CompanyRef:
    """Which filer to fetch: regulator id plus an optional ticker for issuer resolution."""

    cik: str
    ticker: str | None = None
    country_code: str = "US"

    def __post_init__(self) -> None:
        if not self.cik.isdigit() or len(self.cik) > 10:
            raise ValueError(f"CIK must be up to 10 digits, got {self.cik!r}")

    @property
    def padded(self) -> str:
        return self.cik.zfill(10)


@dataclass(frozen=True, slots=True)
class CompanyRequest:
    market: str
    companies: tuple[CompanyRef, ...]
    params: Mapping[str, Any] = field(default_factory=dict, hash=False)


@dataclass(frozen=True, slots=True)
class FundamentalFilingRecord:
    issuer: EntityRef
    accession_number: str
    form_type: str
    filing_date: date
    filing_url: str
    period_end: date | None = None
    period_type: PeriodType = "other"
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    is_amendment: bool = False
    issuer_hint: InstrumentRef | None = None


@dataclass(frozen=True, slots=True)
class LineItemRecord:
    issuer: EntityRef
    accession_number: str
    taxonomy: str
    tag: str
    period_end: date
    value: Decimal
    unit: str
    period_start: date | None = None
    frame: str | None = None
    issuer_hint: InstrumentRef | None = None

    def __post_init__(self) -> None:
        require_decimal("value", self.value, optional=False)
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError(f"{self.tag}: period_start after period_end")


FundamentalsRow = FundamentalFilingRecord | LineItemRecord


@runtime_checkable
class FundamentalsSink(ProviderStorage, Protocol):
    def write_fundamentals(self, rows: Sequence[FundamentalsRow], *,
                           provenance: Provenance) -> WriteResult: ...


__all__ = [
    "CompanyRef",
    "CompanyRequest",
    "FundamentalFilingRecord",
    "FundamentalsRow",
    "FundamentalsSink",
    "LineItemRecord",
    "PeriodType",
]
