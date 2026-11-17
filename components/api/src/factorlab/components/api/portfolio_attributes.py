"""Instrument attributes (sector, ...) that portfolio rows can be pivoted by.

Rows are labelled on the server and grouped in the browser, so a new pivot dimension is
one ``Attribute`` here and no UI change. Every attribute sees the same resolved
instrument: the broker id (IBKR conid) or contract is first mapped to its canonical
listing (a derivative to its underlying), then the attribute classifies that listing.
Classifications are current, not point-in-time.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol
from uuid import UUID

from factorlab.components.api.instruments import current_sector_sql
from factorlab.indexes import load_catalog
from factorlab.storage.v2_broker import BROKER_ALIAS_KIND

log = logging.getLogger(__name__)

NIL_UUID = "00000000-0000-0000-0000-000000000000"
UNCLASSIFIED: dict[str, str | None] = {"id": None, "label": "Unclassified"}
MULTI_SECTOR_ETF: dict[str, str | None] = {"id": "etf", "label": "ETF / multi-sector"}
ETF_TYPES = {"etf", "etn", "ETF"}


class Reader(Protocol):
    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Instrument:
    """One portfolio row's instrument after identity resolution."""

    listing_id: str | None  # the canonical listing, or a derivative's underlying
    symbol: str  # upper-case trading symbol (the listing's when known)
    security_type: str  # ref.securities.security_type, else the broker product type


class Attribute(Protocol):
    id: str
    label: str

    def buckets(self) -> list[dict[str, str | None]]:
        """Every known bucket in display order; Unclassified is always last."""
        ...

    def classify(self, reader: Reader, instruments: Sequence[Instrument]) -> list[dict[str, Any]]:
        """One ``{id, label}`` per instrument, in the same order."""
        ...


@cache
def _sector_etfs() -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for sector in load_catalog().sectors:
        value: dict[str, str | None] = {"id": sector.sector_id, "label": sector.name}
        out[sector.cw_etf.upper()] = value
        out[sector.ew_etf.upper()] = value
    return out


class SectorAttribute:
    """Current GICS sector; sector ETFs take their sector, other ETFs one bucket."""

    id = "sector"
    label = "Sector"

    def buckets(self) -> list[dict[str, str | None]]:
        sectors: list[dict[str, str | None]] = [
            {"id": s.sector_id, "label": s.name} for s in load_catalog().sectors
        ]
        return [*sectors, MULTI_SECTOR_ETF, UNCLASSIFIED]

    def classify(self, reader: Reader, instruments: Sequence[Instrument]) -> list[dict[str, Any]]:
        ids = sorted({item.listing_id for item in instruments if item.listing_id})
        sectors: dict[str, dict[str, Any]] = {}
        if ids:
            for row in reader.query(
                # Never alias toString(x) AS x here: WHERE would compare the String alias.
                f"SELECT toString(listing_id) AS id, sector_id, sector "
                f"FROM ({current_sector_sql()}) WHERE listing_id IN {{ids:Array(UUID)}}",
                {"ids": ids},
            ):
                sectors[str(row["id"])] = {
                    "id": str(row["sector_id"]),
                    "label": str(row["sector"] or row["sector_id"]),
                }
        etfs = _sector_etfs()
        result: list[dict[str, Any]] = []
        for item in instruments:
            if item.listing_id and item.listing_id in sectors:
                result.append(sectors[item.listing_id])
            elif item.symbol in etfs:
                result.append(etfs[item.symbol])
            elif item.security_type in ETF_TYPES:
                result.append(MULTI_SECTOR_ETF)
            else:
                result.append(UNCLASSIFIED)
        return result


ATTRIBUTES: dict[str, Attribute] = {attribute.id: attribute for attribute in (SectorAttribute(),)}


def dimensions() -> list[dict[str, Any]]:
    return [
        {"id": attribute.id, "label": attribute.label, "buckets": attribute.buckets()}
        for attribute in ATTRIBUTES.values()
    ]


def parse_dims(value: str) -> list[str]:
    """``dims=sector,country`` -> known attribute ids; unknown names are ignored."""
    return [name for name in dict.fromkeys(value.split(",")) if name in ATTRIBUTES]


def _uuid(value: Any) -> str | None:
    text = str(value or "")
    if not text or text == NIL_UUID:
        return None
    try:
        return str(UUID(text))
    except ValueError:
        return None


def _ids(row: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(listing_id, contract_id, vendor_id) from a broker row or a PnL detail."""
    listing, contract = _uuid(row.get("listing_id")), _uuid(row.get("contract_id"))
    vendor = str(row.get("vendor_id") or "") or None
    # PnL details carry one ``instrument`` key such as ``vendor_id:265598``.
    kind, _, value = str(row.get("instrument") or "").partition(":")
    if kind == "listing_id":
        listing = listing or _uuid(value)
    elif kind == "contract_id":
        contract = contract or _uuid(value)
    elif kind == "vendor_id" and value:
        vendor = vendor or value
    return listing, contract, vendor


def resolve(reader: Reader, rows: Sequence[dict[str, Any]]) -> list[Instrument]:
    """Map each row to its canonical listing with at most three bounded queries."""
    ids = [_ids(row) for row in rows]
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    by_kind: dict[str, set[str]] = {}
    for row, (listing, contract, vendor) in zip(rows, ids, strict=True):
        kind = BROKER_ALIAS_KIND.get(str(row.get("broker_code") or ""))
        if not listing and not contract and vendor and kind:
            by_kind.setdefault(kind, set()).add(vendor)
    for kind, values in by_kind.items():
        for alias in reader.query(
            "SELECT alias_value, target_kind, toString(target_id) AS target_id "
            "FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = {kind:String} AND alias_value IN {values:Array(String)} "
            "AND target_kind IN ('listing', 'contract') AND valid_from <= today() "
            "AND (valid_to IS NULL OR valid_to >= today()) "
            "ORDER BY alias_value, valid_from DESC",
            {"kind": kind, "values": sorted(values)},
        ):
            aliases.setdefault(
                (kind, str(alias["alias_value"])),
                (str(alias["target_kind"]), str(alias["target_id"])),
            )
    resolved: list[tuple[str | None, str | None]] = []
    for row, (listing, contract, vendor) in zip(rows, ids, strict=True):
        if not listing and not contract and vendor:
            kind = BROKER_ALIAS_KIND.get(str(row.get("broker_code") or ""), "")
            target_kind, target = aliases.get((kind, vendor), ("", ""))
            listing = target if target_kind == "listing" else None
            contract = target if target_kind == "contract" else None
        resolved.append((listing, contract))
    contracts = sorted({c for listing, c in resolved if c and not listing})
    underlying: dict[str, str] = {}
    if contracts:
        underlying = {
            str(row["id"]): str(row["underlying_listing_id"])
            for row in reader.query(
                "SELECT toString(contract_id) AS id, "
                "toString(underlying_listing_id) AS underlying_listing_id "
                "FROM ref.contracts FINAL WHERE contract_id IN {ids:Array(UUID)}",
                {"ids": contracts},
            )
            if _uuid(row["underlying_listing_id"])
        }
    listings = [listing or underlying.get(contract or "") for listing, contract in resolved]
    known: dict[str, dict[str, Any]] = {}
    wanted = sorted({listing for listing in listings if listing})
    if wanted:
        known = {
            str(row["listing_id"]): row
            for row in reader.query(
                "SELECT toString(l.listing_id) AS listing_id, l.trading_symbol AS symbol, "
                "s.security_type AS security_type FROM ref.listings AS l FINAL "
                "LEFT JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
                "WHERE l.listing_id IN {ids:Array(UUID)}",
                {"ids": wanted},
            )
        }
    out: list[Instrument] = []
    for row, listing in zip(rows, listings, strict=True):
        ref = known.get(listing or "", {})
        out.append(
            Instrument(
                listing_id=listing,
                symbol=str(ref.get("symbol") or row.get("trading_symbol") or "").upper(),
                security_type=str(ref.get("security_type") or row.get("product_type") or ""),
            )
        )
    return out


def enrich(
    reader: Reader, rows: Iterable[dict[str, Any]], dims: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows with ``attributes = {dim: {id, label}}``, and the dims that failed to resolve.

    A failed lookup never fails the report: its dimension is named as unavailable and
    left off the rows, so the UI cannot mistake it for Unclassified.
    """
    rows = list(rows)
    if not dims or not rows:
        return rows, []
    try:
        instruments = resolve(reader, rows)
    except Exception:
        log.exception("portfolio instrument resolution failed")
        return rows, list(dims)
    values: dict[str, list[dict[str, Any]]] = {}
    failed: list[str] = []
    for name in dims:
        try:
            values[name] = ATTRIBUTES[name].classify(reader, instruments)
        except Exception:
            log.exception("portfolio attribute %s failed", name)
            failed.append(name)
    enriched = [
        {
            **row,
            "attributes": {
                **(row.get("attributes") or {}),
                **{name: found[index] for name, found in values.items()},
            },
        }
        for index, row in enumerate(rows)
    ]
    return enriched, failed


INSTRUMENT_KEYS: tuple[str, ...] = (
    "broker_code",
    "listing_id",
    "contract_id",
    "vendor_id",
    "trading_symbol",
)


def label_rows(
    reader: Reader, rows: Sequence[dict[str, Any]], dims: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Each row's ``attributes``, resolving every distinct instrument once.

    For long row lists (trades, orders) where many rows share an instrument; the
    returned list is parallel to ``rows``. A dimension that failed is left out.
    """
    keys = [tuple(str(row.get(key) or "") for key in INSTRUMENT_KEYS) for row in rows]
    unique = list(dict.fromkeys(keys))
    labelled, failed = enrich(
        reader, [dict(zip(INSTRUMENT_KEYS, key, strict=True)) for key in unique], dims
    )
    found = {key: row.get("attributes") or {} for key, row in zip(unique, labelled, strict=True)}
    return [found[key] for key in keys], failed


def parse_bucket(value: str) -> tuple[str, str | None] | None:
    """``bucket=sector:45`` -> ("sector", "45"); ``sector:`` is Unclassified; else None."""
    name, separator, bucket = value.partition(":")
    if not separator or name not in ATTRIBUTES:
        return None
    return name, bucket or None


def pivot_groups[T](
    rows: Sequence[T], attributes: Sequence[dict[str, Any]], dim: str
) -> list[tuple[dict[str, Any], list[T]]]:
    """Rows per bucket of ``dim`` in the dimension's display order, Unclassified last."""
    groups: dict[str | None, tuple[dict[str, Any], list[T]]] = {}
    for row, labels in zip(rows, attributes, strict=True):
        value = labels.get(dim) or UNCLASSIFIED
        groups.setdefault(value["id"], (value, []))[1].append(row)
    rank = {bucket["id"]: index for index, bucket in enumerate(ATTRIBUTES[dim].buckets())}
    return sorted(
        groups.values(),
        key=lambda item: (
            item[0]["id"] is None,
            rank.get(item[0]["id"], len(rank)),
            str(item[0]["label"]),
        ),
    )
