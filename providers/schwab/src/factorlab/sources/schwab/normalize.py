"""Pure Schwab -> canonical record normalization (docs/architecture/07 §5, rule R7).

Ports the rules of ``market.normalize`` (the production path) onto records:

* 1min bars are kept only inside the XNYS regular session and only once the
  minute has finished by the capture's ``fetched_at``;
* daily bars are kept once their session had closed by ``fetched_at``;
* a repeated timestamp keeps the last candle.

Invalid candles are excluded. Production requires completeness, preserving usable
rows and marking the unit incomplete when a row fails validation. Schwab's ``/pricehistory`` prices are split-adjusted
by default (``configs/sources/schwab.yaml``), exactly as production stores them.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from factorlab.calendars.us import NY, bounds, calendar
from factorlab.ingest.datasets import BarRecord, InstrumentRecord, InstrumentRef
from factorlab.ingest.errors import NormalizationError, PartialNormalizationError
from factorlab.ingest.identifiers import isin_from_cusip
from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.settings import ALIAS_KIND

log = logging.getLogger(__name__)

COUNTRY = "US"
# Epoch arithmetic, not datetime.fromtimestamp, which rejects pre-1970 (negative)
# timestamps on Windows; Schwab daily history can start before 1970.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PRODUCT = {"EQUITY": "common", "ETF": "etf"}


def provider_symbol(symbol: str) -> str:
    """Canonical ticker (``BRK-B``) -> Schwab's class notation (``BRK/B``)."""
    return symbol.strip().upper().replace(".", "/").replace("-", "/")


def canonical_symbol(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _json(capture: RawCapture) -> Any:
    try:
        return json.loads(capture.body)
    except ValueError as exc:
        raise NormalizationError(f"{capture.request_key}: body is not JSON") from exc


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def ref_to_metadata(ref: InstrumentRef) -> dict[str, Any]:
    return {
        "alias_value": ref.alias_value,
        "exchange_code": ref.exchange_code,
        "trading_symbol": ref.trading_symbol,
        "isin": ref.isin,
    }


def ref_from_metadata(item: Mapping[str, Any]) -> InstrumentRef:
    return InstrumentRef(
        ALIAS_KIND,
        str(item["alias_value"]),
        str(item.get("exchange_code", "")),
        str(item.get("trading_symbol", "")),
        COUNTRY,
        isin=item.get("isin"),
    )


# -- ref.listings (instrument lookup, one symbol per capture) ---------------------------
def normalize_instrument(
    capture: RawCapture, *, exchanges: Mapping[str, str]
) -> list[InstrumentRecord]:
    wanted = str(capture.metadata.get("symbol") or "")
    payload = _json(capture)
    items = payload.get("instruments") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        raise NormalizationError(f"{capture.request_key}: response has no instruments array")
    exact = [item for item in items if isinstance(item, Mapping) and item.get("symbol") == wanted]
    if len(exact) != 1:
        return []  # unknown or ambiguous: nothing to assert about this symbol
    item = exact[0]
    product = _PRODUCT.get(str(item.get("assetType") or "").upper())
    exchange = exchanges.get(str(item.get("exchange") or "").strip().upper())
    if not product or not exchange:
        return []
    cusip = str(item.get("cusip") or "").strip().upper() or None
    symbol = canonical_symbol(wanted)
    return [
        InstrumentRecord(
            ref=InstrumentRef(
                ALIAS_KIND,
                wanted,
                exchange,
                symbol,
                COUNTRY,
                isin=isin_from_cusip(cusip),
                cusip=cusip,
            ),
            name=str(item.get("description") or symbol).strip(),
            product_type=product,
            currency="USD",
        )
    ]


def normalize_quote_listings(
    capture: RawCapture, *, exchanges: Mapping[str, str]
) -> list[InstrumentRecord]:
    """Validate every requested stock from a batched market quote reference."""
    payload = _json(capture)
    if not isinstance(payload, Mapping):
        raise NormalizationError("quotes response is not an object")
    wanted = {canonical_symbol(str(s)) for s in capture.metadata.get("symbols", ())}
    records = {}
    invalid = payload.get("errors", {}).get("invalidSymbols", ())
    invalid = {canonical_symbol(str(s)) for s in invalid}
    for key, value in payload.items():
        if key == "errors" or not isinstance(value, Mapping):
            continue
        symbol = canonical_symbol(str(value.get("symbol") or key))
        if symbol not in wanted or symbol in invalid or value.get("assetMainType") != "EQUITY":
            continue
        reference = value.get("reference") or {}
        venue = (
            str(
                reference.get("exchangeName")
                or reference.get("exchange")
                or value.get("exchangeName")
                or value.get("exchange")
                or ""
            )
            .strip()
            .upper()
        )
        exchange = exchanges.get(venue)
        currency = str(reference.get("currency") or value.get("currency") or "USD").upper()
        if exchange is None or currency != "USD":
            continue
        # Quotes may omit CUSIP. The validated exchange and symbol still resolve
        # existing identity; no guessed identifier is introduced.
        records[symbol] = InstrumentRecord(
            InstrumentRef(ALIAS_KIND, provider_symbol(symbol), exchange, symbol, COUNTRY),
            str(value.get("description") or reference.get("description") or symbol).strip(),
            "common",
            "USD",
        )
    rows = [records[s] for s in sorted(records)]
    if missing := wanted - records.keys():
        raise PartialNormalizationError(
            f"Schwab could not validate {len(missing)} configured equities", rows=rows
        )
    return rows


# -- market.bars (price history, one symbol per capture) -------------------------------
def normalize_pricehistory(capture: RawCapture) -> list[BarRecord]:
    instruments = capture.metadata.get("instruments") or []
    resolution = capture.metadata.get("resolution")
    if len(instruments) != 1 or resolution not in ("daily", "1min"):
        raise NormalizationError(
            f"{capture.request_key}: metadata needs one instrument and a daily/1min resolution"
        )
    ref = ref_from_metadata(instruments[0])
    payload = _json(capture)
    candles = payload.get("candles") if isinstance(payload, Mapping) else None
    if not isinstance(candles, list):
        raise NormalizationError(f"{capture.request_key}: response has no candle array")
    now = capture.fetched_at.astimezone(UTC)
    sessions: dict[date, tuple[datetime, datetime] | None] = {}
    out: dict[datetime, BarRecord] = {}
    invalid = 0
    cal = calendar()
    for candle in candles:
        if not isinstance(candle, Mapping) or "datetime" not in candle:
            continue
        try:
            stamp = _EPOCH + timedelta(milliseconds=int(candle["datetime"]))
        except (TypeError, ValueError, OverflowError) as exc:
            raise NormalizationError(f"{capture.request_key}: bad candle time") from exc
        day = stamp.astimezone(NY).date()
        if stamp > now or not cal.first_session.date() <= day <= cal.last_session.date():
            continue
        if day not in sessions:
            sessions[day] = bounds(day)
        session = sessions[day]
        if session is None:
            continue
        opened, closed = session
        if resolution == "1min":
            if not (opened <= stamp < closed) or stamp + timedelta(minutes=1) > now:
                continue
            if stamp.second or stamp.microsecond:
                log.warning("%s: dropping unaligned minute %s", capture.request_key, stamp)
                invalid += 1
                continue
        elif closed > now:
            continue
        prices = [_decimal(candle.get(name)) for name in ("open", "high", "low", "close")]
        volume = _decimal(candle.get("volume"))
        if (
            any(p is None or p < 0 for p in prices)
            or volume is None
            or volume < 0
            or volume != volume.to_integral_value()
        ):
            log.warning("%s: dropping invalid candle at %s", capture.request_key, stamp)
            invalid += 1
            continue
        o, h, low, c = prices
        if not (low <= o <= h and low <= c <= h):
            log.warning("%s: dropping inconsistent candle at %s", capture.request_key, stamp)
            invalid += 1
            continue
        out[stamp] = BarRecord(ref, resolution, stamp, o, h, low, c, volume=int(volume))
    rows = [out[stamp] for stamp in sorted(out)]
    if invalid and capture.metadata.get("require_complete"):
        raise PartialNormalizationError(f"Invalid Schwab candles: {invalid}", rows=rows)
    return rows


__all__ = [
    "canonical_symbol",
    "normalize_instrument",
    "normalize_pricehistory",
    "provider_symbol",
    "ref_from_metadata",
    "ref_to_metadata",
]
