"""Pure EODHD -> canonical record normalization (docs/architecture/07 §5, rule R7).

Deterministic over the archived bytes and capture metadata. EODHD quirks end
here: ``CODE.US`` symbols, venue names, ``Type`` strings and date-only bars.
Daily bars use EODHD's raw ``close`` (unadjusted, as 06 requires for
``market.bars``); ``adjusted_close`` stays in the raw archive. A day is only
emitted once its XNYS session had closed when the capture was fetched.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from factorlab.calendars.us import NY, bounds
from factorlab.ingest.datasets import (
    BarRecord,
    ConstituentRecord,
    InstrumentRecord,
    InstrumentRef,
)
from factorlab.ingest.errors import NormalizationError
from factorlab.ingest.provider import RawCapture
from factorlab.sources.eodhd.settings import ALIAS_KIND

log = logging.getLogger(__name__)

COUNTRY = "US"
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,13}$")


def canonical_symbol(code: Any) -> str | None:
    """EODHD code -> FactorLab ticker (``BRK-B``); ``None`` if it is not a plain US symbol."""
    symbol = str(code or "").strip().upper().removesuffix(".US").replace("/", "-")
    return symbol if _SYMBOL.fullmatch(symbol) else None


def eodhd_symbol(code: Any) -> str:
    return f"{str(code).strip().upper().removesuffix('.US')}.US"


def _json(capture: RawCapture) -> Any:
    try:
        return json.loads(capture.body)
    except ValueError as exc:
        raise NormalizationError(f"{capture.request_key}: body is not JSON") from exc


def _isin(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if len(text) == 12 and text.isalnum() else None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def ref_to_metadata(ref: InstrumentRef) -> dict[str, Any]:
    return {"alias_value": ref.alias_value, "exchange_code": ref.exchange_code,
            "trading_symbol": ref.trading_symbol, "isin": ref.isin}


def ref_from_metadata(item: Mapping[str, Any]) -> InstrumentRef:
    return InstrumentRef(ALIAS_KIND, str(item["alias_value"]), str(item.get("exchange_code", "")),
                         str(item.get("trading_symbol", "")), COUNTRY, isin=item.get("isin"))


# -- ref.listings ------------------------------------------------------------------
def normalize_listings(capture: RawCapture, *, venues: Mapping[str, str],
                       listing_types: Mapping[str, str]) -> list[InstrumentRecord]:
    data = _json(capture)
    if not isinstance(data, list):
        raise NormalizationError("exchange symbol list is not a JSON array")
    candidates: dict[str, InstrumentRecord] = {}
    conflicts: set[str] = set()
    for item in data:
        if not isinstance(item, Mapping):
            continue
        product = listing_types.get(str(item.get("Type") or "").casefold())
        exchange = venues.get(str(item.get("Exchange") or "").strip().upper())
        symbol = canonical_symbol(item.get("Code"))
        if not product or not exchange or not symbol:
            continue
        if str(item.get("Currency") or "").upper() != "USD":
            continue
        record = InstrumentRecord(
            ref=InstrumentRef(ALIAS_KIND, eodhd_symbol(item["Code"]), exchange, symbol, COUNTRY,
                              isin=_isin(item.get("Isin") or item.get("ISIN"))),
            name=str(item.get("Name") or symbol).strip(), product_type=product, currency="USD",
        )
        previous = candidates.get(symbol)
        if previous is not None and previous != record:
            conflicts.add(symbol)  # two live listings claim one ticker: resolve by hand
        candidates[symbol] = record
    for symbol in sorted(conflicts):
        log.warning("EODHD lists %s more than once with different details; skipping", symbol)
    return [candidates[s] for s in sorted(candidates) if s not in conflicts]


# -- market.bars (daily) -----------------------------------------------------------
def _closed_by(day: date, fetched_at: datetime) -> bool:
    session = bounds(day)
    return session is not None and session[1] <= fetched_at


def _bar(ref: InstrumentRef, item: Mapping[str, Any], fetched_at: datetime) -> BarRecord | None:
    try:
        day = date.fromisoformat(str(item["date"]))
    except (KeyError, ValueError):
        return None
    if not _closed_by(day, fetched_at):
        return None  # holiday, weekend, or a session still in progress
    prices = [_decimal(item.get(name)) for name in ("open", "high", "low", "close")]
    if any(p is None for p in prices):
        return None
    o, h, low, c = prices
    if not (low <= o <= h and low <= c <= h):
        log.warning("dropping inconsistent EODHD bar %s %s", ref.alias_value, day)
        return None
    volume = _decimal(item.get("volume"))
    return BarRecord(ref, "daily", datetime.combine(day, time(0), tzinfo=NY), o, h, low, c,
                     volume=int(volume) if volume is not None and volume >= 0 else None)


def _sorted_unique(bars: Iterable[BarRecord | None]) -> list[BarRecord]:
    by_key: dict[tuple[str, datetime], BarRecord] = {}
    for bar in bars:
        if bar is not None:
            by_key[(bar.instrument.alias_value, bar.bar_time)] = bar
    return [by_key[key] for key in sorted(by_key)]


def normalize_eod(capture: RawCapture) -> list[BarRecord]:
    """``/eod/{CODE}.US`` history for one instrument named in the capture metadata."""
    instruments = capture.metadata.get("instruments") or []
    if len(instruments) != 1:
        raise NormalizationError(f"{capture.request_key}: expected one instrument in metadata")
    ref = ref_from_metadata(instruments[0])
    data = _json(capture)
    if not isinstance(data, list):
        raise NormalizationError(f"{capture.request_key}: eod response is not an array")
    return _sorted_unique(_bar(ref, item, capture.fetched_at)
                          for item in data if isinstance(item, Mapping))


def normalize_bulk(capture: RawCapture) -> list[BarRecord]:
    """``/eod-bulk-last-day/US`` for one date, filtered to the requested instruments."""
    wanted = {item["alias_value"]: ref_from_metadata(item)
              for item in capture.metadata.get("instruments") or []}
    data = _json(capture)
    if not isinstance(data, list):
        raise NormalizationError(f"{capture.request_key}: bulk response is not an array")
    bars = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        ref = wanted.get(eodhd_symbol(item.get("code") or ""))
        if ref is not None:
            bars.append(_bar(ref, item, capture.fetched_at))
    return _sorted_unique(bars)


# -- ref.universe_membership ------------------------------------------------------------
def normalize_components(capture: RawCapture) -> list[ConstituentRecord]:
    """``/fundamentals/{INDEX}?filter=Components`` -> the index's complete current membership."""
    meta = capture.metadata
    code = str(meta["universe"])
    payload = _json(capture)
    if isinstance(payload, Mapping) and "Components" in payload:
        payload = payload["Components"]
    records = list(payload.values()) if isinstance(payload, Mapping) else payload
    if not isinstance(records, list) or not records:
        raise NormalizationError(f"{code}: malformed or empty Components response")
    members: dict[str, ConstituentRecord] = {}
    for item in records:
        if not isinstance(item, Mapping) or not item.get("Code"):
            raise NormalizationError(f"{code}: malformed constituent {item!r}")
        symbol = canonical_symbol(item["Code"])
        if symbol is None:
            raise NormalizationError(f"{code}: unusable symbol {item['Code']!r}")
        members[symbol] = ConstituentRecord(
            code, InstrumentRef(ALIAS_KIND, eodhd_symbol(item["Code"]), "", symbol, COUNTRY),
            universe_name=str(meta.get("name") or ""))
    minimum = int(meta["minimum_constituents"])
    if len(members) < minimum:
        raise NormalizationError(
            f"{code}: {len(members)} constituents is below the minimum of {minimum}")
    return [members[symbol] for symbol in sorted(members)]


__all__ = [
    "canonical_symbol",
    "eodhd_symbol",
    "normalize_bulk",
    "normalize_components",
    "normalize_eod",
    "normalize_listings",
    "ref_from_metadata",
    "ref_to_metadata",
]
