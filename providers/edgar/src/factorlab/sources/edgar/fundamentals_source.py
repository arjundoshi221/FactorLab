"""SEC EDGAR company facts -> ``fundamentals.filings`` (docs/architecture/07 §6, P8).

One unit per company: ``data.sec.gov/api/xbrl/companyfacts/CIK##########.json``.
Filings are derived from the facts' accession numbers (form, filed date, fiscal
year/period); every fact in a configured taxonomy becomes a line item. SEC
requires a self-identifying User-Agent on every request; it is read through
``get_secret`` and a missing one is ``AuthRequired``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

import requests
from pydantic import Field

from factorlab.core.secrets import get_secret
from factorlab.ingest.bindings import ProviderSettings
from factorlab.ingest.datasets import Capabilities, EntityRef, FetchUnit, InstrumentRef
from factorlab.ingest.datasets.fundamentals import (
    CompanyRequest,
    FundamentalFilingRecord,
    FundamentalsRow,
    LineItemRecord,
)
from factorlab.ingest.errors import AuthRequired, NormalizationError, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.ingest.transport import raise_for_status

_PERIOD = {
    "FY": "annual",
    "Q1": "quarterly",
    "Q2": "quarterly",
    "Q3": "quarterly",
    "Q4": "quarterly",
}


class EdgarSettings(ProviderSettings):
    base_url_data: str = "https://data.sec.gov"
    base_url_www: str = "https://www.sec.gov"
    user_agent_env: str = "EDGAR_USER_AGENT"
    requests_per_second: int = Field(default=10, ge=1, le=10)  # SEC fair-access ceiling
    taxonomies: tuple[str, ...] = ("us-gaap", "ifrs-full")
    tags: tuple[str, ...] = ()  # empty = every concept
    timeout: float = 60.0


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


class EdgarCompanyFacts:
    provider: ClassVar[str] = "edgar"
    dataset: ClassVar[str] = "fundamentals.filings"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=frozenset({"USA"}))
    settings_model: ClassVar[type[EdgarSettings]] = EdgarSettings

    def __init__(
        self,
        settings: EdgarSettings,
        *,
        instance: str = "edgar",
        session: requests.Session | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        secret: Callable[[str, str], str | None] = get_secret,
    ) -> None:
        self.settings = settings
        self.instance = instance
        self.session = session or requests.Session()
        self.limiter = SlidingWindowLimiter([(1.0, settings.requests_per_second)])
        self._clock = clock
        self._secret = secret

    def plan(self, request: CompanyRequest) -> Sequence[FetchUnit]:
        return [
            FetchUnit(
                f"companyfacts:{company.padded}",
                f"{self.instance}:companyfacts",
                params={
                    "cik": company.padded,
                    "ticker": company.ticker or "",
                    "country": company.country_code,
                },
            )
            for company in dict.fromkeys(request.companies)
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        agent = (self._secret(self.settings.user_agent_env, "") or "").strip()
        if not agent:
            raise AuthRequired(f"{self.settings.user_agent_env} is missing; SEC requires one")
        cik = str(unit.params["cik"])
        url = f"{self.settings.base_url_data.rstrip('/')}/api/xbrl/companyfacts/CIK{cik}.json"
        self.limiter.acquire()
        try:
            response = self.session.get(
                url,
                headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
                timeout=self.settings.timeout,
            )
        except requests.RequestException as exc:
            raise TransientError(f"EDGAR request failed: {type(exc).__name__}") from exc
        raise_for_status("EDGAR", response.status_code, response.headers, unit.name)
        return RawCapture(
            body=response.content,
            request_key=unit.name,
            transport="http",
            fetched_at=self._clock(),
            source_url=url,
            content_type=response.headers.get("Content-Type", "application/json"),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"},
            metadata={
                "cik": cik,
                "ticker": unit.params.get("ticker") or "",
                "country": unit.params.get("country") or "US",
            },
        )

    def normalize(self, capture: RawCapture) -> Sequence[FundamentalsRow]:
        try:
            payload = json.loads(capture.body)
        except ValueError as exc:
            raise NormalizationError(f"{capture.request_key}: body is not JSON") from exc
        facts = payload.get("facts") if isinstance(payload, dict) else None
        if not isinstance(facts, dict):
            raise NormalizationError(f"{capture.request_key}: response has no facts object")
        cik = str(capture.metadata["cik"]).zfill(10)
        issuer = EntityRef("cik", cik, "issuer", str(payload.get("entityName") or "") or None)
        ticker = str(capture.metadata.get("ticker") or "").strip().upper()
        hint = (
            InstrumentRef("", "", "", ticker, str(capture.metadata.get("country") or "US"))
            if ticker
            else None
        )
        wanted_tags = set(self.settings.tags)
        filings: dict[str, dict[str, Any]] = {}
        items: dict[tuple, LineItemRecord] = {}
        for taxonomy in self.settings.taxonomies:
            for tag, concept in sorted((facts.get(taxonomy) or {}).items()):
                if wanted_tags and tag not in wanted_tags:
                    continue
                for unit, observations in sorted(((concept or {}).get("units") or {}).items()):
                    for fact in observations or []:
                        accn, end = fact.get("accn"), _date(fact.get("end"))
                        value = _value(fact.get("val"))
                        filed = _date(fact.get("filed"))
                        if not accn or end is None or value is None or filed is None:
                            continue
                        start = _date(fact.get("start"))
                        if start is not None and start > end:
                            continue
                        filing = filings.setdefault(
                            accn,
                            {
                                "form": str(fact.get("form") or ""),
                                "filed": filed,
                                "fy": fact.get("fy"),
                                "fp": fact.get("fp"),
                                "end": end,
                            },
                        )
                        filing["end"] = max(filing["end"], end)
                        key = (accn, taxonomy, tag, unit, start, end)
                        items[key] = LineItemRecord(
                            issuer,
                            accn,
                            taxonomy,
                            tag,
                            end,
                            value,
                            unit,
                            period_start=start,
                            frame=fact.get("frame"),
                            issuer_hint=hint,
                        )
        records: list[FundamentalsRow] = []
        for accn in sorted(filings):
            f = filings[accn]
            fp = str(f["fp"]) if f["fp"] else None
            records.append(
                FundamentalFilingRecord(
                    issuer=issuer,
                    accession_number=accn,
                    form_type=f["form"],
                    filing_date=f["filed"],
                    period_end=f["end"],
                    period_type=_PERIOD.get(fp or "", "other"),  # type: ignore[arg-type]
                    fiscal_year=int(f["fy"]) if f["fy"] else None,
                    fiscal_period=fp,
                    is_amendment=f["form"].endswith("/A"),
                    issuer_hint=hint,
                    filing_url=(
                        f"{self.settings.base_url_www.rstrip('/')}/Archives/edgar/data/"
                        f"{int(cik)}/{accn.replace('-', '')}/"
                    ),
                )
            )
        records += [items[key] for key in sorted(items, key=lambda k: tuple(map(str, k)))]
        return records


__all__ = ["EdgarCompanyFacts", "EdgarSettings"]
