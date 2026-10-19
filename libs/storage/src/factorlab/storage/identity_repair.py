"""Retire duplicate listing identities into their canonical FactorLab listing.

Cutover-era code minted a second ``ref.listings`` identity for some symbols that
already had one (same country, exchange and trading symbol, different security).
Market data collects on the listing that carries the provider alias; other datasets
can still point at the orphan. A retirement:

1. records ``ref.listing_migrations`` (``migration_type = 'duplicate_identity'``),
   which resolvers use to skip the orphan;
2. writes a new ``ref.listings`` version of the orphan with ``active = false``;
3. closes the orphan's open aliases and re-opens them on the canonical listing.

Rows stored under the orphan stay where they are; datasets that resolve by symbol
(political trades) re-resolve afterwards. Only provable duplicates are retired: exactly
one listing of the symbol carries the canonical alias, all share an exchange and no
two carry different ISINs. Anything else is reported, never guessed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factorlab.storage.canonical_ids import LISTING_NAMESPACE
from factorlab.storage.clickhouse import _decoded_text, _version

MIGRATION_TYPE = "duplicate_identity"
REPAIR_SOURCE = "factorlab_repair"
# Listing IDs retired as duplicates; resolvers exclude them.
RETIRED_LISTINGS = (
    "SELECT old_listing_id FROM ref.listing_migrations FINAL "
    f"WHERE migration_type = '{MIGRATION_TYPE}'"
)
LISTING_COLUMNS = (
    "listing_id",
    "security_id",
    "exchange_code",
    "country_code",
    "trading_symbol",
    "local_symbol",
    "mic",
    "lot_size",
    "tick_size",
    "is_primary",
    "first_traded",
    "last_traded",
    "active",
    "version",
    "ingested_at",
)
ALIAS_COLUMNS = (
    "alias_kind",
    "alias_value",
    "scope_country",
    "scope_exchange",
    "target_kind",
    "target_id",
    "source",
    "valid_from",
    "valid_to",
    "confidence",
    "notes",
    "version",
    "ingested_at",
)


@dataclass(frozen=True, slots=True)
class Retirement:
    symbol: str
    exchange: str
    orphan: uuid.UUID
    canonical: uuid.UUID


@dataclass
class RetireReport:
    retirements: list[Retirement] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    aliases_moved: int = 0
    applied: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "retired_listings": len(self.retirements),
            "symbols": len({r.symbol for r in self.retirements}),
            "aliases_moved": self.aliases_moved,
            "skipped": self.skipped,
            "retirements": [
                {
                    "symbol": r.symbol,
                    "exchange": r.exchange,
                    "orphan": str(r.orphan),
                    "canonical": str(r.canonical),
                }
                for r in self.retirements
            ],
        }


def plan_duplicate_listings(
    client: Any, *, country: str, canonical_alias_kind: str
) -> RetireReport:
    rows = client.query(
        "SELECT l.listing_id, l.trading_symbol, l.exchange_code, ifNull(s.isin, '') AS isin, "
        "l.listing_id IN (SELECT target_id FROM ref.identifier_aliases FINAL "
        "WHERE alias_kind = {kind:String} AND target_kind = 'listing' AND valid_to IS NULL) "
        "AS canonical FROM ref.listings AS l FINAL "
        "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
        "WHERE l.country_code = {country:String} AND l.active "
        f"AND l.listing_id NOT IN ({RETIRED_LISTINGS}) "
        "AND l.trading_symbol IN (SELECT trading_symbol FROM ref.listings FINAL "
        "WHERE country_code = {country:String} AND active GROUP BY trading_symbol "
        "HAVING count() > 1) ORDER BY l.trading_symbol, l.listing_id",
        parameters={"kind": canonical_alias_kind, "country": country},
    ).result_rows
    groups: dict[str, list[tuple[uuid.UUID, str, str, bool]]] = defaultdict(list)
    for listing_id, symbol, exchange, isin, canonical in rows:
        groups[_decoded_text(symbol)].append(
            (listing_id, _decoded_text(exchange), _decoded_text(isin), bool(canonical))
        )
    report = RetireReport()
    for symbol, listings in sorted(groups.items()):
        keepers = [item for item in listings if item[3]]
        if len(keepers) != 1:
            report.skipped[symbol] = (
                f"{len(keepers)} listings carry {canonical_alias_kind}; need exactly one"
            )
            continue
        if len({item[1] for item in listings}) != 1:
            report.skipped[symbol] = "listings are on different exchanges"
            continue
        if len({item[2] for item in listings if item[2]}) > 1:
            report.skipped[symbol] = "listings carry different ISINs"
            continue
        keeper = keepers[0]
        report.retirements.extend(
            Retirement(symbol, keeper[1], item[0], keeper[0])
            for item in listings
            if item is not keeper
        )
    return report


def retire_duplicate_listings(
    client: Any,
    *,
    country: str,
    canonical_alias_kind: str,
    apply: bool = False,
    now: datetime | None = None,
) -> RetireReport:
    """Plan (and with ``apply``, write) the retirement of provable duplicate listings."""
    report = plan_duplicate_listings(
        client, country=country, canonical_alias_kind=canonical_alias_kind
    )
    orphans = {r.orphan: r for r in report.retirements}
    if not orphans:
        report.applied = apply
        return report
    aliases = client.query(
        f"SELECT {', '.join(ALIAS_COLUMNS[:-2])} FROM ref.identifier_aliases FINAL "
        "WHERE target_kind = 'listing' AND target_id IN {ids:Array(UUID)} AND valid_to IS NULL",
        parameters={"ids": sorted(orphans, key=str)},
    ).result_rows
    report.aliases_moved = len(aliases)
    if not apply:
        return report
    now = now or datetime.now(UTC)
    today, version = now.date(), _version(now)
    _insert(
        client,
        "ref.listing_migrations",
        [
            {
                "migration_id": uuid.uuid5(
                    LISTING_NAMESPACE, f"factorlab:{MIGRATION_TYPE}:{r.orphan}:{r.canonical}"
                ),
                "old_listing_id": r.orphan,
                "new_listing_id": r.canonical,
                "effective_date": today,
                "migration_type": MIGRATION_TYPE,
                "price_adjustment": None,
                "volume_adjustment": None,
                "source": REPAIR_SOURCE,
                "raw_id": None,
                "notes": (
                    f"{r.symbol} on {r.exchange}: duplicate identity; the canonical listing "
                    f"carries {canonical_alias_kind}"
                ),
                "as_of_time": now,
                "version": version,
                "ingested_at": now,
            }
            for r in report.retirements
        ],
    )
    listings = client.query(
        f"SELECT {', '.join(LISTING_COLUMNS)} FROM ref.listings FINAL "
        "WHERE listing_id IN {ids:Array(UUID)}",
        parameters={"ids": sorted(orphans, key=str)},
    ).result_rows
    _insert(
        client,
        "ref.listings",
        [
            {
                **dict(zip(LISTING_COLUMNS, row, strict=True)),
                "active": False,
                "version": version,
                "ingested_at": now,
            }
            for row in listings
        ],
    )
    moved: list[dict[str, Any]] = []
    for row in aliases:
        alias = dict(zip(ALIAS_COLUMNS[:-2], row, strict=True))
        retirement = orphans[alias["target_id"]]
        # Aliases are keyed by (kind, value, valid_from): one opened today is retargeted
        # in place rather than closed and reopened under the same key.
        if alias["valid_from"] != today:
            moved.append({**alias, "valid_to": today, "version": version, "ingested_at": now})
        moved.append(
            {
                **alias,
                "target_id": retirement.canonical,
                "source": REPAIR_SOURCE,
                "valid_from": today,
                "valid_to": None,
                "notes": f"moved from retired duplicate listing {retirement.orphan}",
                "version": version,
                "ingested_at": now,
            }
        )
    _insert(client, "ref.identifier_aliases", moved)
    report.applied = True
    return report


def _insert(client: Any, table: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    columns = list(records[0])
    client.insert(
        table, [[row[column] for column in columns] for row in records], column_names=columns
    )
