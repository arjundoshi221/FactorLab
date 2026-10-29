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

A later purge removes the retired tombstones: bars only the orphan holds are copied to
the canonical listing, aliases on the orphan's security and entity (SEC CIKs) move to the
canonical ones, then the orphan's bars, recovery state, aliases, listing, and any
security or entity nothing else references are deleted. ``ref.listing_migrations`` keeps
the record.
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


@dataclass
class PurgeReport:
    orphans: list[tuple[str, uuid.UUID, uuid.UUID]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    bars_rescued: int = 0
    bars_deleted: int = 0
    recovery_rows_deleted: int = 0
    aliases_moved: int = 0
    securities_deleted: int = 0
    entities_deleted: int = 0
    applied: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "orphans": len(self.orphans),
            "bars_rescued": self.bars_rescued,
            "bars_deleted": self.bars_deleted,
            "recovery_rows_deleted": self.recovery_rows_deleted,
            "aliases_moved": self.aliases_moved,
            "securities_deleted": self.securities_deleted,
            "entities_deleted": self.entities_deleted,
            "skipped": self.skipped,
            "purged": [
                {"symbol": symbol, "orphan": str(orphan), "canonical": str(canonical)}
                for symbol, orphan, canonical in self.orphans
            ],
        }


def purge_retired_listings(
    client: Any, *, country: str, apply: bool = False, now: datetime | None = None
) -> PurgeReport:
    """Plan (and with ``apply``, delete) the tombstones of retired duplicate listings.

    The orphan's listing row is deleted last, so a run that fails part way is found and
    finished by the next one; the bar copy and alias moves are idempotent.
    """
    rows = client.query(
        "SELECT o.trading_symbol, m.old_listing_id, m.new_listing_id, o.active, o.security_id, "
        "n.security_id, ns.entity_id FROM ref.listing_migrations AS m FINAL "
        "INNER JOIN ref.listings AS o FINAL ON o.listing_id = m.old_listing_id "
        "INNER JOIN ref.listings AS n FINAL ON n.listing_id = m.new_listing_id "
        "INNER JOIN ref.securities AS ns FINAL ON ns.security_id = n.security_id "
        f"WHERE m.migration_type = '{MIGRATION_TYPE}' AND o.country_code = {{country:String}} "
        "ORDER BY o.trading_symbol, m.old_listing_id",
        parameters={"country": country},
    ).result_rows
    report = PurgeReport()
    canonical: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = {}
    # orphan security -> canonical (security, entity)
    orphan_securities: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
    for symbol, orphan, new, active, security, new_security, new_entity in rows:
        symbol = _decoded_text(symbol)
        if active:
            report.skipped[f"{symbol}:{orphan}"] = "retired listing is active again"
            continue
        report.orphans.append((symbol, orphan, new))
        canonical[orphan] = (new, new_security, new_entity)
        orphan_securities[security] = (new_security, new_entity)
    if not canonical:
        report.applied = apply
        return report
    ids = sorted(canonical, key=str)
    now = now or datetime.now(UTC)
    version = _version(now)

    # Bars: copy what only the orphan holds, then everything under the orphan goes.
    counts = client.query(
        "SELECT listing_id, count() FROM market.bars "
        "WHERE country_code = {country:String} AND listing_id IN {ids:Array(UUID)} "
        "GROUP BY listing_id",
        parameters={"country": country, "ids": ids},
    ).result_rows
    rescued: list[dict[str, Any]] = []
    for orphan, count in counts:
        report.bars_deleted += int(count)
        new, new_security, new_entity = canonical[orphan]
        result = client.query(
            "SELECT * FROM market.bars FINAL "
            "WHERE country_code = {country:String} AND listing_id = {orphan:UUID} "
            "AND (resolution, source, bar_time) NOT IN (SELECT resolution, source, bar_time "
            "FROM market.bars FINAL WHERE country_code = {country:String} "
            "AND listing_id = {canonical:UUID})",
            parameters={"country": country, "orphan": orphan, "canonical": new},
        )
        rescued.extend(
            {
                **dict(zip(result.column_names, row, strict=True)),
                "listing_id": new,
                "security_id": new_security,
                "entity_id": new_entity,
                "version": version,
                "ingested_at": now,
            }
            for row in result.result_rows
        )
    report.bars_rescued = len(rescued)
    [(recovery,)] = client.query(
        "SELECT count() FROM meta.recovery_state FINAL WHERE listing_id IN {ids:Array(UUID)}",
        parameters={"ids": ids},
    ).result_rows
    report.recovery_rows_deleted = int(recovery)

    # A security or entity goes only when nothing outside the orphans references it, and
    # its aliases move only when it maps to exactly one canonical identity.
    securities = client.query(
        "SELECT security_id, entity_id FROM ref.securities FINAL "
        "WHERE security_id IN {ids:Array(UUID)}",
        parameters={"ids": sorted(orphan_securities, key=str)},
    ).result_rows
    shared_securities = {
        row[0]
        for row in client.query(
            "SELECT DISTINCT security_id FROM ref.listings FINAL "
            "WHERE security_id IN {sids:Array(UUID)} AND listing_id NOT IN {ids:Array(UUID)}",
            parameters={"sids": sorted(orphan_securities, key=str), "ids": ids},
        ).result_rows
    }
    entity_targets: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for security, entity in securities:
        if security not in shared_securities:
            entity_targets[entity].add(orphan_securities[security][1])
    purged_securities = {
        security for security, _ in securities if security not in shared_securities
    }
    shared_entities = {
        row[0]
        for row in client.query(
            "SELECT DISTINCT entity_id FROM ref.securities FINAL "
            "WHERE entity_id IN {eids:Array(UUID)} AND security_id NOT IN {sids:Array(UUID)}",
            parameters={
                "eids": sorted(entity_targets, key=str),
                "sids": sorted(purged_securities, key=str),
            },
        ).result_rows
    }
    targets: dict[uuid.UUID, uuid.UUID] = {
        security: orphan_securities[security][0] for security in purged_securities
    }
    for entity, news in sorted(entity_targets.items(), key=lambda item: str(item[0])):
        if entity in shared_entities:
            continue
        if len(news) != 1:
            report.skipped[f"entity:{entity}"] = (
                f"maps to {len(news)} canonical entities; kept with its aliases"
            )
            continue
        targets[entity] = next(iter(news))
    report.securities_deleted = len(purged_securities)
    report.entities_deleted = sum(1 for target in targets if target not in purged_securities)

    aliases = client.query(
        f"SELECT {', '.join(ALIAS_COLUMNS[:-2])} FROM ref.identifier_aliases FINAL "
        "WHERE target_kind IN ('security', 'entity') AND target_id IN {ids:Array(UUID)}",
        parameters={"ids": sorted({*targets, *targets.values()}, key=str)},
    ).result_rows
    held = {
        (alias["alias_kind"], alias["alias_value"], alias["target_id"])
        for alias in (dict(zip(ALIAS_COLUMNS[:-2], row, strict=True)) for row in aliases)
        if alias["valid_to"] is None
    }
    moved: list[dict[str, Any]] = []
    for row in aliases:
        alias = dict(zip(ALIAS_COLUMNS[:-2], row, strict=True))
        target = targets.get(alias["target_id"])
        if target is None or alias["valid_to"] is not None:
            continue
        if (alias["alias_kind"], alias["alias_value"], target) in held:
            continue
        # Same (kind, value, valid_from) key: a newer version retargets it in place.
        moved.append(
            {
                **alias,
                "target_id": target,
                "source": REPAIR_SOURCE,
                "notes": f"moved from purged duplicate {alias['target_kind']} {alias['target_id']}",
                "version": version,
                "ingested_at": now,
            }
        )
    report.aliases_moved = len(moved)
    if not apply:
        return report

    _insert(client, "market.bars", rescued)
    _insert(client, "ref.identifier_aliases", moved)
    _delete(client, "market.bars", "listing_id", ids)
    _delete(client, "meta.recovery_state", "listing_id", ids)
    _delete(client, "ref.identifier_aliases", "target_id", ids, "target_kind = 'listing'")
    if targets:
        _delete(
            client,
            "ref.identifier_aliases",
            "target_id",
            sorted(targets, key=str),
            "target_kind IN ('security', 'entity')",
        )
    entities = sorted((t for t in targets if t not in purged_securities), key=str)
    if entities:
        _delete(client, "ref.entities", "entity_id", entities)
    if purged_securities:
        _delete(client, "ref.securities", "security_id", sorted(purged_securities, key=str))
    _delete(client, "ref.listings", "listing_id", ids)
    report.applied = True
    return report


def _delete(
    client: Any, table: str, column: str, ids: list[uuid.UUID], condition: str = ""
) -> None:
    where = f"{condition} AND " if condition else ""
    for start in range(0, len(ids), 1000):
        client.command(
            f"DELETE FROM {table} WHERE {where}{column} IN {{ids:Array(UUID)}}",
            parameters={"ids": ids[start : start + 1000]},
        )


def _insert(client: Any, table: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    columns = list(records[0])
    client.insert(
        table, [[row[column] for column in columns] for row in records], column_names=columns
    )
