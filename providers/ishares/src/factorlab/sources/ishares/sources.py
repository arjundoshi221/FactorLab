"""iShares fund holdings: ``plan -> fetch -> normalize`` (docs/architecture/07 §6).

One ``latest-holdings.csv`` per configured fund serves two datasets:

* ``ref.universe_membership``: the fund's listed US equity holdings as a membership
  *proxy* (IWV samples the Russell 3000; it is not the licensed index roster), with
  the fund weight;
* ``ref.security_sectors``: each holding's GICS sector, as iShares labels it, mapped to
  the GICS level-2 code.

The file is a short preamble (fund name, ``Fund Holdings as of``, ...) followed by a
CSV table. Cash, futures, unlisted and OTC lines, placeholder tickers (``-``) and
rights / warrants / CVRs / escrows are not equities a collector can price, so they
are dropped. A holdings date older than ``max_age_days`` fails normalization.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
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
    SectorRecord,
)
from factorlab.ingest.errors import NormalizationError, PermanentError, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.transport import raise_for_status

ALLOWED_HOSTS = frozenset({"www.ishares.com"})
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,13}$")
_NOT_COMMON = re.compile(r"\b(RIGHTS?|WARRANTS?|CVR|ESCROW|WHEN ISSUED)\b")
DEFAULT_EXCHANGES = ("NYSE", "NASDAQ", "Nyse Mkt Llc", "Cboe BZX")

# iShares sector labels -> (GICS level-2 code, GICS name).
GICS_SECTORS = {
    "Energy": ("10", "Energy"),
    "Materials": ("15", "Materials"),
    "Industrials": ("20", "Industrials"),
    "Consumer Discretionary": ("25", "Consumer Discretionary"),
    "Consumer Staples": ("30", "Consumer Staples"),
    "Health Care": ("35", "Health Care"),
    "Financials": ("40", "Financials"),
    "Information Technology": ("45", "Information Technology"),
    "Communication": ("50", "Communication Services"),
    "Communication Services": ("50", "Communication Services"),
    "Utilities": ("55", "Utilities"),
    "Real Estate": ("60", "Real Estate"),
}


class HoldingsFund(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    name: str = ""
    minimum_constituents: int = Field(ge=1)
    max_age_days: int = Field(default=7, ge=1)
    exchanges: tuple[str, ...] = DEFAULT_EXCHANGES


class IsharesSettings(ProviderSettings):
    """Non-secret settings from configs/sources/ishares.yaml; ``funds`` keys are universe codes."""

    funds: dict[str, HoldingsFund] = Field(default_factory=dict)
    timeout: float = 30.0


def validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise PermanentError("iShares holdings URLs must use HTTPS on www.ishares.com")


def normalize_symbol(value: str) -> str | None:
    """``BRK B`` / ``BRK.B`` / ``BRK/B`` -> ``BRK-B``; ``None`` if not a usable ticker."""
    symbol = re.sub(r"[ ./]", "-", value.strip().upper())
    return symbol if _SYMBOL.fullmatch(symbol) else None


def _holdings_date(preamble: list[str], code: str) -> date:
    for line in preamble:
        label, _, value = line.partition(",")
        if label.strip() == "Fund Holdings as of":
            try:
                return datetime.strptime(
                    f"{value.strip().strip(chr(34))} +0000", "%b %d, %Y %z"
                ).date()
            except ValueError as exc:
                raise NormalizationError(f"{code}: unreadable holdings date {value!r}") from exc
    raise NormalizationError(f"{code}: holdings file has no 'Fund Holdings as of' line")


def _weight(value: str) -> Decimal | None:
    try:
        number = Decimal(value.replace(",", "")) / 100
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def parse_holdings(capture: RawCapture) -> tuple[str, list[dict[str, str]]]:
    """Return (universe code, equity rows a collector can price), validating the file."""
    meta = capture.metadata
    code = str(meta["universe"])
    try:
        lines = capture.body.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise NormalizationError(f"{code}: holdings file is not UTF-8") from exc
    header = next((i for i, line in enumerate(lines) if line.startswith("Ticker,")), None)
    if header is None:
        raise NormalizationError(f"{code}: holdings file has no Ticker header (not a CSV?)")
    as_of = _holdings_date(lines[:header], code)
    age = (capture.fetched_at.astimezone(UTC).date() - as_of).days
    if not 0 <= age <= int(meta["max_age_days"]):
        raise NormalizationError(f"{code}: holdings dated {as_of} are stale or future-dated")
    exchanges = set(meta["exchanges"])
    rows: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO("\n".join(lines[header:]))):
        if row.get("Asset Class") != "Equity" or row.get("Exchange") not in exchanges:
            continue
        if _NOT_COMMON.search(str(row.get("Name") or "").upper()):
            continue
        symbol = normalize_symbol(str(row.get("Ticker") or ""))
        if symbol is None:
            continue
        if symbol in rows:
            raise NormalizationError(f"{code}: {symbol} is listed twice")
        rows[symbol] = row
    minimum = int(meta["minimum_constituents"])
    if len(rows) < minimum:
        raise NormalizationError(
            f"{code}: {len(rows)} listed equities is below the minimum of {minimum}"
        )
    return code, [{**row, "Ticker": symbol} for symbol, row in sorted(rows.items())]


class _IsharesHoldings:
    provider: ClassVar[str] = "ishares"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=frozenset({"USA"}))
    settings_model: ClassVar[type[IsharesSettings]] = IsharesSettings

    def __init__(
        self,
        settings: IsharesSettings,
        *,
        instance: str = "ishares",
        session: requests.Session | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.instance = instance
        self.session = session or requests.Session()
        self._clock = clock

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        codes = request.params.get("universes") or sorted(self.settings.funds)
        unknown = sorted(set(codes) - set(self.settings.funds))
        if unknown:
            raise ValueError(f"no iShares fund configured for {unknown}")
        return [
            FetchUnit(f"holdings:{code}", f"{self.instance}:holdings", params={"universe": code})
            for code in codes
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        code = str(unit.params["universe"])
        fund = self.settings.funds[code]
        validate_url(fund.url)
        try:
            response = self.session.get(
                fund.url,
                timeout=self.settings.timeout,
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (FactorLab research)"},
            )
        except requests.RequestException as exc:
            raise TransientError(f"iShares request failed: {type(exc).__name__}") from exc
        validate_url(response.url)  # a redirect must stay on iShares
        raise_for_status("iShares", response.status_code, response.headers, unit.name)
        return RawCapture(
            body=response.content,
            request_key=unit.name,
            transport="http",
            fetched_at=self._clock(),
            source_url=response.url,
            content_type=response.headers.get("Content-Type", "text/csv"),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"},
            metadata={"universe": code, **fund.model_dump(mode="json")},
        )

    @staticmethod
    def _ref(symbol: str) -> InstrumentRef:
        return InstrumentRef("", "", "", symbol, "US")


class IsharesUniverse(_IsharesHoldings):
    dataset: ClassVar[str] = "ref.universe_membership"

    def normalize(self, capture: RawCapture) -> Sequence[ConstituentRecord]:
        code, rows = parse_holdings(capture)
        name = str(capture.metadata.get("name") or "")
        return [
            ConstituentRecord(
                code,
                self._ref(row["Ticker"]),
                universe_name=name,
                weight=_weight(str(row.get("Weight (%)") or "")),
            )
            for row in rows
        ]


class IsharesSectors(_IsharesHoldings):
    dataset: ClassVar[str] = "ref.security_sectors"

    def normalize(self, capture: RawCapture) -> Sequence[SectorRecord]:
        _, rows = parse_holdings(capture)
        records = []
        for row in rows:
            sector = GICS_SECTORS.get(str(row.get("Sector") or "").strip())
            if sector is None:
                continue  # unlabelled holdings stay unclassified
            records.append(SectorRecord(self._ref(row["Ticker"]), sector[0], sector[1], "gics"))
        return records


__all__ = [
    "GICS_SECTORS",
    "IsharesSectors",
    "IsharesSettings",
    "IsharesUniverse",
    "normalize_symbol",
    "parse_holdings",
    "validate_url",
]
