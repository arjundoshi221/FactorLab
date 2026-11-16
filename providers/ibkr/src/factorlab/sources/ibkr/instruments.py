"""IBKR instruments as FactorLab reference records, from Flex statement identity.

Every Flex row that names a stock carries IBKR's own identity for it: ``conid``,
``isin``, ``figi``, ``listingExchange``, ``subCategory`` and ``description``. Each
distinct conid becomes one :class:`InstrumentRecord` (alias ``ibkr_conid``), which
the DB service links to the FactorLab listing (alias, then ISIN, then exchange +
ticker) or mints when no listing exists yet. Broker facts then resolve through the
alias, so every IBKR instrument is a FactorLab ticker.

Only stocks and ETFs on venues mapped below are emitted. A venue missing from
:data:`EXCHANGES` is skipped and logged, and its rows stay unresolved, so a new
venue is one line here (plus its ``ref.exchanges`` row).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from xml.etree.ElementTree import Element

from factorlab.ingest.datasets import InstrumentRef
from factorlab.ingest.datasets.reference import InstrumentRecord

log = logging.getLogger(__name__)

ALIAS_KIND = "ibkr_conid"

# IBKR listingExchange -> (MIC, country). Resolution and minting accept the MIC.
EXCHANGES: Mapping[str, tuple[str, str]] = {
    "NASDAQ": ("XNAS", "US"),
    "NYSE": ("XNYS", "US"),
    "ARCA": ("ARCX", "US"),
    "AMEX": ("XASE", "US"),
    "BATS": ("BATS", "US"),
    "LSE": ("XLON", "GB"),
    "LSEETF": ("XLON", "GB"),
}

# IBKR subCategory -> FactorLab product type (stocks only; anything else is "common").
SUB_CATEGORIES: Mapping[str, str] = {
    "COMMON": "common",
    "ETF": "etf",
    "ETN": "etn",
    "ADR": "adr",
    "PREFERRED": "preferred",
    "REIT": "reit",
    "WARRANT": "warrant",
}


def factorlab_symbol(symbol: str) -> str:
    """IBKR share classes use a space (``MOG A``, ``BRK B``); FactorLab tickers a dash."""
    return "-".join(
        part for part in symbol.strip().upper().replace(".", " ").replace("/", " ").split()
    )


def _isin(value: str) -> str | None:
    value = value.strip().upper()
    return value if len(value) == 12 and value.isalnum() else None


def instrument_record(attributes: Mapping[str, str]) -> InstrumentRecord | None:
    """One stock's reference record, or ``None`` when the row cannot name a listing."""
    conid = attributes.get("conid", "").strip()
    if not conid or attributes.get("assetCategory", "").upper() != "STK":
        return None
    venue = attributes.get("listingExchange", "").strip().upper()
    symbol = factorlab_symbol(attributes.get("symbol", ""))
    currency = attributes.get("currency", "").strip().upper()
    if venue not in EXCHANGES:
        log.info("IBKR venue %r not mapped; conid %s stays unresolved", venue, conid)
        return None
    if not symbol or len(currency) != 3:
        return None
    mic, country = EXCHANGES[venue]
    return InstrumentRecord(
        ref=InstrumentRef(
            alias_kind=ALIAS_KIND,
            alias_value=conid,
            exchange_code=mic,
            trading_symbol=symbol,
            country_code=country,
            isin=_isin(attributes.get("isin", "")),
            cusip=attributes.get("cusip") or None,
            figi=attributes.get("figi") or None,
        ),
        name=attributes.get("description", "").strip() or symbol,
        product_type=SUB_CATEGORIES.get(attributes.get("subCategory", "").upper(), "common"),
        currency=currency,
    )


def instrument_records(elements: Iterable[Element]) -> list[InstrumentRecord]:
    """Distinct stocks named anywhere in a statement; the most complete row per conid wins."""
    best: dict[str, InstrumentRecord] = {}
    for element in elements:
        record = instrument_record(element.attrib)
        if record is None:
            continue
        known = best.get(record.ref.alias_value)
        if known is None or (record.ref.isin and not known.ref.isin):
            best[record.ref.alias_value] = record
    return [best[conid] for conid in sorted(best)]


__all__ = [
    "ALIAS_KIND",
    "EXCHANGES",
    "SUB_CATEGORIES",
    "factorlab_symbol",
    "instrument_record",
    "instrument_records",
]
