"""Provider-neutral identity resolution (docs/architecture/07 §8.1).

Resolution order for an :class:`InstrumentRef`:

1. alias      ``ref.identifier_aliases`` (alias_kind, alias_value), open-ended -> ``exact``
2. ISIN       ``ref.securities.isin`` + ``ref.listings.exchange_code``        -> ``high``
3. symbol     ``ref.listings`` exchange_code + trading_symbol, ISIN-compatible -> ``medium``

Minting (step 4 in the spec) is the caller's decision; this module only reads.
Lookups are batched per alias kind and cached for the resolver's lifetime,
which is one sink call.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from factorlab.ingest.datasets import InstrumentRef
from factorlab.storage.clickhouse import _decoded_text


@dataclass(frozen=True, slots=True)
class ListingIdentity:
    listing_id: UUID
    security_id: UUID
    entity_id: UUID | None
    product_type: str
    country_code: str
    confidence: str


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    contract_id: UUID
    underlying_listing_id: UUID
    country_code: str
    confidence: str


def _product_type(security_type: Any) -> str:
    text = _decoded_text(security_type)
    return "common" if text == "equity" else text


class IdentityResolver:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._listings: dict[InstrumentRef, ListingIdentity] = {}
        self._contracts: dict[InstrumentRef, ContractIdentity] = {}

    # -- listings ------------------------------------------------------------
    def _alias_targets(
        self, refs: list[InstrumentRef], target_kind: str
    ) -> dict[InstrumentRef, UUID]:
        found: dict[InstrumentRef, UUID] = {}
        by_kind: dict[str, list[InstrumentRef]] = {}
        for ref in refs:
            if ref.has_alias:
                by_kind.setdefault(ref.alias_kind, []).append(ref)
        for kind, group in by_kind.items():
            rows = self.client.query(
                "SELECT alias_value, target_id, valid_from FROM ref.identifier_aliases FINAL "
                "WHERE alias_kind = {kind:String} AND target_kind = {target:String} "
                "AND valid_to IS NULL AND alias_value IN {values:Array(String)}",
                parameters={
                    "kind": kind,
                    "target": target_kind,
                    "values": sorted({ref.alias_value for ref in group}),
                },
            ).result_rows
            latest: dict[str, tuple[Any, UUID]] = {}
            for value, target, valid_from in rows:
                value = _decoded_text(value)
                if value not in latest or (valid_from or date.min) > (latest[value][0] or date.min):
                    latest[value] = (valid_from, target)
            for ref in group:
                if ref.alias_value in latest:
                    found[ref] = latest[ref.alias_value][1]
        return found

    def _natural_listing(self, ref: InstrumentRef) -> tuple[UUID, str] | None:
        # Blank exchange = country-scoped lookup (e.g. bare index tickers).
        scope, scope_params = (
            ("l.exchange_code = {exchange:String}", {"exchange": ref.exchange_code})
            if ref.exchange_code
            else ("l.country_code = {country:String}", {"country": ref.country_code})
        )
        if ref.isin:
            rows = self.client.query(
                "SELECT l.listing_id FROM ref.listings AS l FINAL "
                "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
                f"WHERE s.isin = {{isin:String}} AND {scope} AND l.active",
                parameters={"isin": ref.isin, **scope_params},
            ).result_rows
            if len(rows) == 1:
                return rows[0][0], "high"
            if len(rows) > 1:
                return None  # ambiguous: never guess
        if not ref.trading_symbol:
            return None
        rows = self.client.query(
            "SELECT l.listing_id, s.isin FROM ref.listings AS l FINAL "
            "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
            f"WHERE {scope} AND l.trading_symbol = {{symbol:String}} AND l.active",
            parameters={"symbol": ref.trading_symbol, **scope_params},
        ).result_rows
        if len(rows) != 1:
            return None
        listing_id, isin = rows[0]
        known_isin = _decoded_text(isin) if isin else ""
        if ref.isin and known_isin and known_isin != ref.isin:
            return None  # same ticker, different security (ticker reuse)
        return listing_id, "medium"

    def listings(self, refs: Iterable[InstrumentRef]) -> dict[InstrumentRef, ListingIdentity]:
        refs = list(dict.fromkeys(refs))
        pending = [ref for ref in refs if ref not in self._listings]
        if pending:
            targets = {
                ref: (target, "exact")
                for ref, target in self._alias_targets(pending, "listing").items()
            }
            for ref in pending:
                if ref not in targets:
                    natural = self._natural_listing(ref)
                    if natural is not None:
                        targets[ref] = natural
            ids = sorted({target for target, _ in targets.values()}, key=str)
            details: dict[UUID, tuple[Any, ...]] = {}
            if ids:
                for row in self.client.query(
                    "SELECT l.listing_id, l.security_id, s.entity_id, s.security_type, "
                    "l.country_code FROM ref.listings AS l FINAL "
                    "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
                    "WHERE l.listing_id IN {ids:Array(UUID)}",
                    parameters={"ids": ids},
                ).result_rows:
                    details[row[0]] = row
            for ref, (target, confidence) in targets.items():
                row = details.get(target)
                if row is None:
                    continue  # alias points at a listing that no longer exists
                self._listings[ref] = ListingIdentity(
                    listing_id=row[0],
                    security_id=row[1],
                    entity_id=row[2],
                    product_type=_product_type(row[3]),
                    country_code=_decoded_text(row[4]),
                    confidence=confidence,
                )
        return {ref: self._listings[ref] for ref in refs if ref in self._listings}

    def remember_listing(self, ref: InstrumentRef, identity: ListingIdentity) -> None:
        self._listings[ref] = identity

    # -- contracts -----------------------------------------------------------
    def contracts(self, refs: Iterable[InstrumentRef]) -> dict[InstrumentRef, ContractIdentity]:
        refs = list(dict.fromkeys(refs))
        pending = [ref for ref in refs if ref not in self._contracts]
        if pending:
            targets = self._alias_targets(pending, "contract")
            ids = sorted(set(targets.values()), key=str)
            details: dict[UUID, tuple[Any, ...]] = {}
            if ids:
                for row in self.client.query(
                    "SELECT contract_id, underlying_listing_id, country_code "
                    "FROM ref.contracts FINAL WHERE contract_id IN {ids:Array(UUID)}",
                    parameters={"ids": ids},
                ).result_rows:
                    details[row[0]] = row
            for ref, target in targets.items():
                row = details.get(target)
                if row is not None:
                    self._contracts[ref] = ContractIdentity(
                        contract_id=row[0],
                        underlying_listing_id=row[1],
                        country_code=_decoded_text(row[2]),
                        confidence="exact",
                    )
        return {ref: self._contracts[ref] for ref in refs if ref in self._contracts}

    def natural_future(self, *, underlying_listing_id: UUID, expiry: date) -> UUID | None:
        rows = self.client.query(
            "SELECT contract_id FROM ref.contracts FINAL "
            "WHERE underlying_listing_id = {listing:UUID} AND contract_type = 'future' "
            "AND expiry = {expiry:Date} AND isNull(strike)",
            parameters={"listing": underlying_listing_id, "expiry": expiry},
        ).result_rows
        return rows[0][0] if len(rows) == 1 else None

    def remember_contract(self, ref: InstrumentRef, identity: ContractIdentity) -> None:
        self._contracts[ref] = identity


__all__ = ["ContractIdentity", "IdentityResolver", "ListingIdentity"]
