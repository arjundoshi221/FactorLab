"""House Clerk -> ``alt.political_filings`` and ``alt.political_trades`` (docs/architecture/07 §6).

* Filings: one annual ZIP/XML index per year (``params.years``, default the
  current year). Each PTR row names its filer; the name becomes a
  ``legislator_name`` entity hint that the DB service resolves.
* Trades: one PTR PDF per filing in a ``FilingsRequest`` (built from
  ``PoliticalReader.recent_filings``). Tickers are canonicalised
  (``BRK.B`` -> ``BRK-B``) for resolution; the raw ticker is kept.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urlparse

import requests

from factorlab.shared.ingest.bindings import ProviderSettings
from factorlab.shared.ingest.datasets import (
    Capabilities,
    EntityRef,
    FetchUnit,
    InstrumentRef,
    ReferenceRequest,
)
from factorlab.shared.ingest.datasets.political import (
    FilingsRequest,
    PoliticalFilingRecord,
    PoliticalTradeRecord,
)
from factorlab.shared.ingest.errors import NormalizationError, PermanentError, TransientError
from factorlab.shared.ingest.identifiers import legislator_name_key
from factorlab.shared.ingest.provider import RawCapture
from factorlab.shared.ingest.transport import raise_for_status
from factorlab.sources.house_clerk.parse import (
    PARSER_VERSION,
    parse_house_filing_index,
    parse_house_ptr_text,
    pdf_text,
)

MARKETS = frozenset({"USA"})
ALLOWED_HOST = "disclosures-clerk.house.gov"
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,13}$")


class HouseClerkSettings(ProviderSettings):
    index_url: str = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"
    timeout: float = 60.0


def canonical_ticker(value: str | None) -> str | None:
    text = str(value or "").strip().upper().replace(".", "-").replace("/", "-")
    return text if _TICKER.fullmatch(text) else None


class _HouseClerkSource:
    provider: ClassVar[str] = "house_clerk"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=MARKETS)
    settings_model: ClassVar[type[HouseClerkSettings]] = HouseClerkSettings

    def __init__(self, settings: HouseClerkSettings, *, instance: str = "house_clerk",
                 session: requests.Session | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.settings = settings
        self.instance = instance
        self.session = session or requests.Session()
        self._clock = clock

    def _get(self, url: str, what: str, **metadata: object) -> RawCapture:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            raise PermanentError(f"refusing non-House-Clerk URL for {what}")
        try:
            response = self.session.get(url, timeout=self.settings.timeout)
        except requests.RequestException as exc:
            raise TransientError(f"House Clerk request failed: {type(exc).__name__}") from exc
        raise_for_status("House Clerk", response.status_code, response.headers, what)
        return RawCapture(
            body=response.content, request_key=what, transport="http", fetched_at=self._clock(),
            source_url=url, content_type=response.headers.get("Content-Type", ""),
            status_code=response.status_code, headers=dict(response.headers),
            metadata=dict(metadata),
        )


class HouseClerkFilings(_HouseClerkSource):
    dataset: ClassVar[str] = "alt.political_filings"

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        years = request.params.get("years") or [self._clock().year]
        return [FetchUnit(f"index:{int(year)}", f"{self.instance}:filing_index",
                          params={"year": int(year)}) for year in dict.fromkeys(years)]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        year = int(unit.params["year"])
        return self._get(self.settings.index_url.format(year=year), unit.name, filing_year=year)

    def normalize(self, capture: RawCapture) -> Sequence[PoliticalFilingRecord]:
        try:
            filings = parse_house_filing_index(capture.body, int(capture.metadata["filing_year"]))
        except (KeyError, ValueError, StopIteration, OSError) as exc:
            raise NormalizationError(f"{capture.request_key}: unreadable filing index") from exc
        records = []
        for filing in filings:
            key = legislator_name_key(filing["filer_first_name"], filing["filer_last_name"])
            records.append(PoliticalFilingRecord(
                filing_id=filing["filing_id"], chamber="house",
                filing_type=filing["filing_type"], filing_year=filing["filing_year"],
                filing_date=filing["filing_date"], filer_name_raw=filing["filer_name_raw"],
                filing_url=filing["filing_url"],
                filer=EntityRef("legislator_name", key, "person_legislator",
                                filing["filer_name_raw"]) if key else None,
            ))
        return records


class HouseClerkTrades(_HouseClerkSource):
    dataset: ClassVar[str] = "alt.political_trades"

    def plan(self, request: FilingsRequest) -> Sequence[FetchUnit]:
        return [FetchUnit(f"ptr:{filing.filing_id}", f"{self.instance}:ptr",
                          params={"filing_id": filing.filing_id, "year": filing.filing_year,
                                  "url": filing.filing_url})
                for filing in dict.fromkeys(request.filings)]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        return self._get(str(unit.params["url"]), unit.name,
                         filing_id=unit.params["filing_id"], filing_year=unit.params["year"])

    def normalize(self, capture: RawCapture) -> Sequence[PoliticalTradeRecord]:
        filing_id = str(capture.metadata["filing_id"])
        try:
            text = pdf_text(capture.body)
        except Exception as exc:  # pdfminer raises many types for corrupt PDFs
            raise NormalizationError(f"{capture.request_key}: unreadable PTR PDF") from exc
        records = []
        for trade in parse_house_ptr_text(text, {"filing_id": filing_id}):
            ticker = canonical_ticker(trade["ticker"])
            records.append(PoliticalTradeRecord(
                native_key=trade["trade_key"], filing_id=filing_id, chamber="house",
                transaction_date=trade["transaction_date"],
                asset_name_raw=trade["asset_name_raw"],
                asset_type_code=trade["asset_type_code"],
                transaction_type=trade["transaction_type"], ticker_raw=trade["ticker"],
                instrument=InstrumentRef("", "", "", ticker, "US") if ticker else None,
                notification_date=trade["notification_date"],
                owner_code=trade["owner_code"], filer_type=trade["filer_type"],
                amount_min=trade["amount_min"], amount_max=trade["amount_max"],
                amount_str=trade["amount_str"], parser_version=PARSER_VERSION,
            ))
        return records


__all__ = ["HouseClerkFilings", "HouseClerkSettings", "HouseClerkTrades", "canonical_ticker"]
