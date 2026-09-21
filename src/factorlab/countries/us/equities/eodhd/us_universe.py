"""Normalization for the active US listed-common-stock universe."""

from __future__ import annotations

import math
import re
from datetime import date

import pandas as pd

VENUE_MAP = {
    "NASDAQ": ("XNAS", "Nasdaq Stock Market"),
    "NYSE": ("XNYS", "New York Stock Exchange"),
    "NYSE MKT": ("XASE", "NYSE American"),
    "AMEX": ("XASE", "NYSE American"),
    "NYSE AMERICAN": ("XASE", "NYSE American"),
}
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,13}$")


def canonical_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper().removesuffix(".US").replace("/", "-")
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"Unsupported US symbol: {value!r}")
    return symbol


def schwab_symbol(symbol: str) -> str:
    """Translate the canonical class separator to Schwab's slash notation."""
    return canonical_symbol(symbol).replace(".", "/").replace("-", "/")


def normalize_master(records: list[dict]) -> list[dict]:
    """Keep active USD common stocks/ADRs on the three selected exchanges."""
    normalized = {}
    for item in records:
        if str(item.get("Type", "")).casefold() != "common stock":
            continue
        if str(item.get("Currency", "")).upper() != "USD":
            continue
        venue = str(item.get("Exchange", "")).strip().upper()
        if venue not in VENUE_MAP:
            continue
        try:
            symbol = canonical_symbol(item.get("Code"))
        except ValueError:
            continue
        exchange_code, exchange_name = VENUE_MAP[venue]
        candidate = {
            "symbol": symbol,
            "provider_symbol": f"{str(item['Code']).strip().upper()}.US",
            "name": str(item.get("Name") or symbol).strip(),
            "isin": item.get("Isin") or item.get("ISIN") or None,
            "exchange_code": exchange_code,
            "exchange_name": exchange_name,
        }
        old = normalized.get(symbol)
        if old is not None and old != candidate:
            raise ValueError(f"Conflicting active listings for symbol {symbol}")
        normalized[symbol] = candidate
    return [normalized[key] for key in sorted(normalized)]


def normalize_daily(records: list[dict]) -> pd.DataFrame:
    rows = []
    for item in records:
        try:
            trade_date = date.fromisoformat(str(item["date"])[:10])
            values = {key: float(item[key]) for key in ("open", "high", "low", "close")}
            volume = item["volume"]
            adjusted = item.get("adjusted_close")
            if (not all(math.isfinite(value) and value >= 0 for value in values.values())
                    or values["high"] < max(values.values())
                    or values["low"] > min(values.values())
                    or not isinstance(volume, (int, float)) or not math.isfinite(volume)
                    or volume < 0 or int(volume) != volume
                    or (adjusted is not None and not math.isfinite(float(adjusted)))):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid EODHD daily candle") from exc
        rows.append({"trade_date": trade_date, **values, "adj_close": adjusted,
                     "volume": int(volume)})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates("trade_date", keep="last").sort_values("trade_date")


def normalize_bulk(records: list[dict], lookup: dict[str, object], raw_id=None) -> list[dict]:
    """Normalize a bulk EOD response and retain only active-master symbols."""
    output = []
    seen = set()
    for item in records:
        try:
            symbol = canonical_symbol(item.get("code") or item.get("symbol"))
        except ValueError:
            continue
        if symbol not in lookup or symbol in seen:
            continue
        try:
            frame = normalize_daily([item])
        except ValueError:
            # One malformed listing must not invalidate an otherwise usable
            # exchange-wide snapshot. The missing symbol is surfaced by
            # coverage and picked up by per-symbol recovery.
            continue
        if frame.empty:
            continue
        row = frame.iloc[0].to_dict()
        output.append({"instrument_id": lookup[symbol], "symbol": symbol,
                       "raw_id": raw_id, **row})
        seen.add(symbol)
    return output
