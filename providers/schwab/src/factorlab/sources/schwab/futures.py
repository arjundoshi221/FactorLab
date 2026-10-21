"""Pure Schwab futures helpers: symbols, contract discovery and quote normalization (F-043).

Schwab names a futures root ``/ES`` and a dated contract ``/ESZ26`` (root, CME month
code, two-digit year). Quoting a root returns its active contract. Discovery quotes
each root together with the next few contracts of its listing cycle and keeps the
active contract and the ones after it; nothing here touches the network or the clock.

Field names follow Schwab's ``QuoteFuture`` and ``ReferenceFuture`` schemas. The
settlement field is read from the quote first and the reference second, because
which one a live response populates is still to be confirmed against a recorded
capture (F-043 acceptance).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from factorlab.calendars.us import NY
from factorlab.ingest.datasets import (
    ContractQuoteRecord,
    ContractRecord,
    InstrumentRecord,
    InstrumentRef,
)
from factorlab.ingest.datasets.reference import FUTURE_PRODUCT_TYPES
from factorlab.ingest.errors import NormalizationError
from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.normalize import COUNTRY, _decimal, _json
from factorlab.sources.schwab.settings import ALIAS_KIND, FUTURES_VENUES

log = logging.getLogger(__name__)

MONTH_CODES = "FGHJKMNQUVXZ"  # January .. December
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ROOT = re.compile(r"/[A-Z0-9]{1,4}")
_CONTRACT = re.compile(r"(?P<root>/[A-Z0-9]{1,4}?)(?P<month>[FGHJKMNQUVXZ])(?P<year>\d{2})")


@dataclass(frozen=True, slots=True)
class FuturesRoot:
    """One roster entry: which contracts of a root to record and how to describe it."""

    root: str
    mic: str
    product_type: str
    months: str  # listing cycle as CME month codes, e.g. "HMUZ"
    name: str = ""
    depth: int = 2  # active contract plus the next ``depth - 1``

    def __post_init__(self) -> None:
        if not _ROOT.fullmatch(self.root):
            raise ValueError(f"futures root must look like /ES, got {self.root!r}")
        if self.mic not in FUTURES_VENUES:
            raise ValueError(f"{self.root}: unknown futures venue {self.mic!r}")
        if self.product_type not in FUTURE_PRODUCT_TYPES:
            raise ValueError(f"{self.root}: {self.product_type!r} is not a futures product type")
        if not self.months or set(self.months) - set(MONTH_CODES):
            raise ValueError(f"{self.root}: months must be CME month codes, got {self.months!r}")
        if self.depth < 1:
            raise ValueError(f"{self.root}: depth must be positive")

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> FuturesRoot:
        return cls(
            root=str(item["root"]).upper(),
            mic=str(item["mic"]).upper(),
            product_type=str(item["product_type"]),
            months=str(item["months"]).upper(),
            name=str(item.get("name") or ""),
            depth=int(item.get("depth", 2)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "mic": self.mic,
            "product_type": self.product_type,
            "months": self.months,
            "name": self.name,
            "depth": self.depth,
        }

    def ref(self) -> InstrumentRef:
        return futures_ref(self.root, self.mic)


def futures_ref(symbol: str, mic: str) -> InstrumentRef:
    return InstrumentRef(ALIAS_KIND, symbol, mic, symbol, COUNTRY)


def contract_symbol(root: str, year: int, month: int) -> str:
    return f"{root}{MONTH_CODES[month - 1]}{year % 100:02d}"


def parse_contract(symbol: str) -> tuple[str, int, int] | None:
    """``/ESZ26`` -> ``("/ES", 2026, 12)``; ``None`` for anything else (roots included)."""
    match = _CONTRACT.fullmatch(symbol.strip().upper())
    if match is None:
        return None
    month = MONTH_CODES.index(match.group("month")) + 1
    return match.group("root"), 2000 + int(match.group("year")), month


def cycle_contracts(root: FuturesRoot, as_of: date, count: int) -> list[str]:
    """The next ``count`` contracts of ``root``'s cycle, from ``as_of``'s month on."""
    out: list[str] = []
    year, month = as_of.year, as_of.month
    while len(out) < count:
        if MONTH_CODES[month - 1] in root.months:
            out.append(contract_symbol(root.root, year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def candidate_symbols(root: FuturesRoot, as_of: date) -> list[str]:
    """What discovery quotes for ``root``: the root and enough contracts to roll past one."""
    return [root.root, *cycle_contracts(root, as_of, root.depth + 2)]


def _when(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return _EPOCH + timedelta(milliseconds=millis) if millis > 0 else None


def _count(value: Any) -> int | None:
    number = _decimal(value)
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def _price(value: Any) -> Decimal | None:
    # Schwab reports 0.0 for a level it does not have (an untraded month's open/high/low).
    number = _decimal(value)
    return number if number is not None and number > 0 else None


def _futures(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Requested symbol -> its quote item, for valid futures quotes only."""
    errors = payload.get("errors")
    invalid = (errors or {}).get("invalidSymbols", ()) if isinstance(errors, Mapping) else ()
    invalid = {str(s).upper() for s in invalid}
    return {
        str(key).upper(): value
        for key, value in payload.items()
        if key != "errors"
        and isinstance(value, Mapping)
        and value.get("assetMainType") == "FUTURE"
        and str(key).upper() not in invalid
    }


def _root_item(
    quotes: Mapping[str, Mapping[str, Any]], root: FuturesRoot
) -> tuple[str, Mapping[str, Any]] | None:
    """The quote Schwab returned for ``root``: its active contract, keyed by that symbol.

    Quoting ``/ES`` answers with ``/ESZ26`` (``reference.product == "/ES"``,
    ``futureIsActive``), not with an ``/ES`` key; a root key is still accepted.
    """
    if (item := quotes.get(root.root)) is not None:
        return str(item.get("symbol") or root.root).upper(), item
    for symbol, item in quotes.items():
        reference = item.get("reference") or {}
        if (
            str(reference.get("product") or "").upper() == root.root
            and reference.get("futureIsActive") is True
        ):
            return symbol, item
    return None


def _roots(capture: RawCapture) -> list[FuturesRoot]:
    roots = capture.metadata.get("roots")
    if not isinstance(roots, list) or not roots:
        raise NormalizationError(f"{capture.request_key}: metadata names no futures roots")
    return [FuturesRoot.from_mapping(item) for item in roots]


def _payload(capture: RawCapture) -> Mapping[str, Any]:
    payload = _json(capture)
    if not isinstance(payload, Mapping):
        raise NormalizationError(f"{capture.request_key}: quotes response is not an object")
    return payload


# -- ref.listings: one listing per quoted root --------------------------------------
def normalize_futures_roots(capture: RawCapture) -> list[InstrumentRecord]:
    quotes = _futures(_payload(capture))
    records: list[InstrumentRecord] = []
    for root in _roots(capture):
        found = _root_item(quotes, root)
        if found is None:
            log.warning(
                "%s: Schwab returned no quote for futures root %s", capture.request_key, root.root
            )
            continue
        reference = found[1].get("reference") or {}
        records.append(
            InstrumentRecord(
                root.ref(),
                name=root.name or str(reference.get("description") or root.root).strip(),
                product_type=root.product_type,
                currency="USD",
            )
        )
    return records


# -- ref.contracts: the active contract and the next ones of each root ---------------
def _contract_rows(
    root: FuturesRoot, quotes: Mapping[str, Mapping[str, Any]], today: date
) -> Iterator[ContractRecord]:
    found = _root_item(quotes, root)
    active = found[0] if found is not None else ""
    dated: list[tuple[date, str, Mapping[str, Any]]] = []
    for symbol, item in quotes.items():
        parsed = parse_contract(symbol)
        if parsed is None or parsed[0] != root.root:
            continue
        reference = item.get("reference") or {}
        expires = _when(reference.get("futureExpirationDate"))
        if expires is None:
            continue
        expiry = expires.astimezone(NY).date()
        if expiry >= today:
            dated.append((expiry, symbol, item))
    dated.sort()
    active_expiry = next((expiry for expiry, symbol, _ in dated if symbol == active), None)
    if active_expiry is not None:
        dated = [row for row in dated if row[0] >= active_expiry]
    for expiry, symbol, item in dated[: root.depth]:
        reference = item.get("reference") or {}
        quote = item.get("quote") or {}
        multiplier = _count(reference.get("futureMultiplier"))
        yield ContractRecord(
            ref=futures_ref(symbol, root.mic),
            underlying=root.ref(),
            product_type=root.product_type,
            expiry=expiry,
            multiplier=multiplier or 1,
            tick_size=_price(quote.get("tick")) or None,
        )


def normalize_futures_contracts(capture: RawCapture) -> list[ContractRecord]:
    quotes = _futures(_payload(capture))
    today = capture.fetched_at.astimezone(NY).date()
    records: list[ContractRecord] = []
    for root in _roots(capture):
        rows = list(_contract_rows(root, quotes, today))
        if not rows:
            log.warning("%s: no live contracts quoted for %s", capture.request_key, root.root)
        records.extend(rows)
    return records


# -- market.futures_quotes -----------------------------------------------------------
def normalize_futures_quotes(capture: RawCapture) -> list[ContractQuoteRecord]:
    venues = capture.metadata.get("contracts")
    if not isinstance(venues, Mapping) or not venues:
        raise NormalizationError(f"{capture.request_key}: metadata names no futures contracts")
    quotes = _futures(_payload(capture))
    records: list[ContractQuoteRecord] = []
    for symbol, mic in sorted(venues.items()):
        item = quotes.get(str(symbol).upper())
        if item is None:
            log.warning("%s: Schwab returned no quote for %s", capture.request_key, symbol)
            continue
        quote = item.get("quote") or {}
        reference = item.get("reference") or {}
        settlement = quote.get("settlementPrice")
        if settlement is None:
            settlement = reference.get("futureSettlementPrice")
        observed = (
            _when(quote.get("quoteTime")) or _when(quote.get("tradeTime")) or capture.fetched_at
        )
        records.append(
            ContractQuoteRecord(
                futures_ref(str(symbol).upper(), str(mic)),
                observed.astimezone(UTC),
                last_price=_price(quote.get("lastPrice")),
                last_size=_count(quote.get("lastSize")),
                last_trade_at=_when(quote.get("tradeTime")),
                bid=_price(quote.get("bidPrice")),
                ask=_price(quote.get("askPrice")),
                bid_size=_count(quote.get("bidSize")),
                ask_size=_count(quote.get("askSize")),
                day_open=_price(quote.get("openPrice")),
                day_high=_price(quote.get("highPrice")),
                day_low=_price(quote.get("lowPrice")),
                day_volume=_count(quote.get("totalVolume")),
                oi=_count(quote.get("openInterest")),
                settlement_price=_price(settlement),
            )
        )
    if venues and not records:
        raise NormalizationError(f"{capture.request_key}: no requested futures were quoted")
    return records


def batches(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


__all__ = [
    "MONTH_CODES",
    "FuturesRoot",
    "batches",
    "candidate_symbols",
    "contract_symbol",
    "cycle_contracts",
    "futures_ref",
    "normalize_futures_contracts",
    "normalize_futures_quotes",
    "normalize_futures_roots",
    "parse_contract",
]
