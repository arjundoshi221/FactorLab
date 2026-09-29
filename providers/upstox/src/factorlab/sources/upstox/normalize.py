"""Pure Upstox -> canonical record normalization (docs/architecture/07 §5, rule R7).

Every function is deterministic over the archived bytes and the capture
metadata: no clock, no network, no storage. That is what lets any
``raw.archive`` row be replayed exactly. Upstox quirks end here: segment
codes, epoch-millisecond expiries, candle arrays, the ``prev_ohlc`` /
``live_ohlc`` quote shape and the IST offsets in candle timestamps.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from factorlab.ingest.datasets import (
    BarRecord,
    ContractBarRecord,
    ContractRecord,
    InstrumentRecord,
    InstrumentRef,
)
from factorlab.ingest.errors import NormalizationError
from factorlab.ingest.provider import RawCapture
from factorlab.sources.upstox.settings import ALIAS_KIND

log = logging.getLogger(__name__)

COUNTRY = "IN"
CURRENCY = "INR"
_UNDERLYING_PRODUCT = {"EQUITY": "single_stock_future", "INDEX": "index_future"}


# -- helpers ---------------------------------------------------------------------
def _json(capture: RawCapture) -> Any:
    body = capture.body
    try:
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return json.loads(body)
    except (OSError, ValueError) as exc:
        raise NormalizationError(f"{capture.request_key}: body is not (gzipped) JSON") from exc


def _isin(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if len(text) == 12 and text.isalnum() else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _count(value: Any) -> int | None:
    number = _decimal(value)
    if number is None or number < 0:
        return None
    return int(number)


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
        str(item["exchange_code"]),
        str(item["trading_symbol"]),
        COUNTRY,
        isin=item.get("isin"),
    )


def _instrument_ref(item: Mapping[str, Any], exchange: str) -> InstrumentRef:
    return InstrumentRef(
        ALIAS_KIND,
        str(item["instrument_key"]),
        str(item.get("exchange") or exchange),
        str(item["trading_symbol"]),
        COUNTRY,
        isin=_isin(item.get("isin")),
    )


def _master(capture: RawCapture) -> tuple[str, list[Mapping[str, Any]]]:
    data = _json(capture)
    if not isinstance(data, list):
        raise NormalizationError("instrument master is not a JSON array")
    return str(capture.metadata.get("exchange") or "NSE"), [
        item for item in data if isinstance(item, Mapping)
    ]


# -- reference -------------------------------------------------------------------
def normalize_listings(
    capture: RawCapture, *, instrument_types: Iterable[str] = ("EQ",)
) -> list[InstrumentRecord]:
    exchange, items = _master(capture)
    types = frozenset(instrument_types)
    records: list[InstrumentRecord] = []
    for item in items:
        if item.get("segment") != f"{exchange}_EQ" or item.get("instrument_type") not in types:
            continue
        try:
            ref = _instrument_ref(item, exchange)
            records.append(
                InstrumentRecord(
                    ref=ref,
                    name=str(item.get("name") or ref.trading_symbol),
                    product_type="common",
                    currency=CURRENCY,
                    lot_size=int(item.get("lot_size") or 1),
                    tick_size=_decimal(item.get("tick_size")),
                    attributes={
                        key: str(item[key])
                        for key in ("exchange_token", "security_type", "instrument_type")
                        if key in item
                    },
                )
            )
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed Upstox instrument %r", item.get("instrument_key"))
    return records


def _expiry(value: Any) -> date | None:
    if not value:
        return None
    # Same convention as storage.clickhouse._epoch_ms_to_date: the UTC date of the epoch.
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date()


def normalize_contracts(
    capture: RawCapture, *, underlying_types: Iterable[str] = ("EQUITY",)
) -> list[ContractRecord]:
    exchange, items = _master(capture)
    allowed = frozenset(underlying_types)
    records: list[ContractRecord] = []
    for item in items:
        if item.get("segment") != f"{exchange}_FO" or item.get("instrument_type") != "FUT":
            continue
        underlying_type = str(item.get("underlying_type") or "")
        if underlying_type not in allowed or underlying_type not in _UNDERLYING_PRODUCT:
            continue
        expiry = _expiry(item.get("expiry"))
        underlying_key = str(item.get("underlying_key") or "")
        underlying_symbol = str(item.get("underlying_symbol") or "")
        if expiry is None or not (underlying_key or underlying_symbol):
            continue
        underlying_isin = (
            _isin(underlying_key.partition("|")[2]) if "_EQ|" in underlying_key else None
        )
        try:
            records.append(
                ContractRecord(
                    ref=InstrumentRef(
                        ALIAS_KIND,
                        str(item["instrument_key"]),
                        exchange,
                        str(item["trading_symbol"]),
                        COUNTRY,
                    ),
                    underlying=InstrumentRef(
                        ALIAS_KIND if underlying_key else "",
                        underlying_key,
                        exchange,
                        underlying_symbol,
                        COUNTRY,
                        isin=underlying_isin,
                    ),
                    product_type=_UNDERLYING_PRODUCT[underlying_type],
                    expiry=expiry,
                    lot_size=int(item.get("lot_size") or 1),
                    tick_size=_decimal(item.get("tick_size")),
                    weekly=bool(item.get("weekly", False)),
                    attributes={
                        key: str(item[key])
                        for key in ("exchange_token", "asset_symbol")
                        if key in item
                    },
                )
            )
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed Upstox contract %r", item.get("instrument_key"))
    return records


# -- bars ------------------------------------------------------------------------
def _prices(values: Sequence[Any]) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    prices = tuple(_decimal(value) for value in values)
    if any(price is None for price in prices):
        return None
    o, h, low, c = prices  # type: ignore[misc]
    if not (low <= o <= h and low <= c <= h):
        return None
    return o, h, low, c


def _candle_rows(capture: RawCapture) -> list[tuple[datetime, tuple, int | None, int | None]]:
    payload = _json(capture)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    candles = data.get("candles") if isinstance(data, Mapping) else None
    if not isinstance(candles, list):
        raise NormalizationError(f"{capture.request_key}: response has no data.candles array")
    rows: dict[datetime, tuple[datetime, tuple, int | None, int | None]] = {}
    for candle in candles:
        try:
            stamp = datetime.fromisoformat(str(candle[0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise NormalizationError(f"{capture.request_key}: malformed candle {candle!r}") from exc
        if stamp.tzinfo is None:
            raise NormalizationError(f"{capture.request_key}: candle time without offset")
        prices = _prices(candle[1:5]) if len(candle) >= 5 else None
        if prices is None:
            log.warning("%s: dropping inconsistent candle at %s", capture.request_key, stamp)
            continue
        bar_time = stamp.astimezone(UTC)
        volume = _count(candle[5]) if len(candle) > 5 else None
        oi = _count(candle[6]) if len(candle) > 6 else None
        rows[bar_time] = (bar_time, prices, volume, oi)  # a repeated minute keeps the last
    return [rows[key] for key in sorted(rows)]


def _quote_rows(capture: RawCapture) -> list[tuple[str, datetime, tuple, int | None]]:
    """Finalized minutes from the v3 OHLC quote: ``prev_ohlc`` always, ``live_ohlc`` if closed."""
    payload = _json(capture)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise NormalizationError(f"{capture.request_key}: quote response has no data object")
    current_minute = capture.fetched_at.astimezone(UTC).replace(second=0, microsecond=0)
    rows: list[tuple[str, datetime, tuple, int | None]] = []
    for quote in data.values():
        if not isinstance(quote, Mapping) or not quote.get("instrument_token"):
            continue  # response keys are SEGMENT:SYMBOL and cannot be mapped back safely
        key = str(quote["instrument_token"])
        seen: dict[datetime, tuple[str, datetime, tuple, int | None]] = {}
        for name in ("prev_ohlc", "live_ohlc"):
            candle = quote.get(name)
            if not isinstance(candle, Mapping) or candle.get("ts") is None:
                continue
            try:
                bar_time = datetime.fromtimestamp(int(candle["ts"]) / 1000, tz=UTC)
            except (TypeError, ValueError, OverflowError) as exc:
                raise NormalizationError(f"{capture.request_key}: bad ts for {key}") from exc
            if name == "live_ohlc" and bar_time >= current_minute:
                continue  # still forming
            prices = _prices([candle.get(field) for field in ("open", "high", "low", "close")])
            if prices is None:
                continue
            seen[bar_time] = (key, bar_time, prices, _count(candle.get("volume")))
        rows += [seen[stamp] for stamp in sorted(seen)]
    return rows


def normalize_bars(
    capture: RawCapture, *, contract: bool = False
) -> list[BarRecord] | list[ContractBarRecord]:
    refs = {
        item["alias_value"]: ref_from_metadata(item)
        for item in capture.metadata.get("instruments", [])
    }
    if not refs:
        raise NormalizationError(f"{capture.request_key}: capture metadata names no instruments")
    record = ContractBarRecord if contract else BarRecord
    endpoint = capture.metadata.get("endpoint")
    out: list[Any] = []
    if endpoint == "quote":
        for key, bar_time, (o, h, low, c), volume in _quote_rows(capture):
            if key in refs:
                out.append(
                    record(refs[key], "1min", bar_time, o, h, low, c, volume=volume, oi=None)
                )
        return out
    if endpoint not in ("intraday", "historical"):
        raise NormalizationError(f"{capture.request_key}: unknown endpoint {endpoint!r}")
    ref = next(iter(refs.values()))
    for bar_time, (o, h, low, c), volume, oi in _candle_rows(capture):
        out.append(record(ref, "1min", bar_time, o, h, low, c, volume=volume, oi=oi))
    return out


__all__ = [
    "normalize_bars",
    "normalize_contracts",
    "normalize_listings",
    "ref_from_metadata",
    "ref_to_metadata",
]
