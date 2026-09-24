"""Canonical v2 reference writes for live market collectors."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from factorlab.storage.canonical_ids import canonical_ids, contract_id
from factorlab.storage.clickhouse import _decoded_text, _version


class UnresolvedReference(ValueError):
    """The provider record cannot yet be linked to an approved v2 reference."""


def _one(result: Any) -> tuple[Any, ...] | None:
    rows = result.result_rows
    if len(rows) != 1:
        return None
    return rows[0]


class V2ReferenceWriter:
    def __init__(self, client: Any) -> None:
        self.client = client

    def _insert(self, table: str, record: Mapping[str, Any]) -> None:
        columns = list(record)
        self.client.insert(table, [[record[column] for column in columns]], column_names=columns)

    def upsert_listing(
        self,
        instrument: Mapping[str, Any],
        *,
        alias_kind: str,
        alias_value: str,
        source: str,
        observed_at: datetime | None = None,
    ) -> tuple[UUID, UUID, UUID]:
        """Use the migration resolver's IDs after checking required dimensions."""
        now = observed_at or datetime.now(UTC)
        country = str(instrument["country_code"])
        exchange = str(instrument["exchange_code"])
        currency = str(instrument["currency_code"])
        exchange_row = _one(self.client.query(
            "SELECT mic, country_code, currency_code FROM ref.exchanges FINAL "
            "WHERE exchange_code = {exchange:String} AND active",
            parameters={"exchange": exchange},
        ))
        if exchange_row is None or _decoded_text(exchange_row[1]) != country or _decoded_text(exchange_row[2]) != currency:
            raise UnresolvedReference(f"exchange/currency unresolved: {exchange}")
        currency_row = _one(self.client.query(
            "SELECT currency_code FROM ref.currencies FINAL "
            "WHERE currency_code = {currency:String}",
            parameters={"currency": currency},
        ))
        if currency_row is None:
            raise UnresolvedReference(f"currency unresolved: {currency}")

        entity_id, security_id, listing_id = canonical_ids(instrument)
        version = _version(now)
        entity_prior = _one(self.client.query(
            "SELECT first_seen, last_seen, legal_name FROM ref.entities FINAL "
            "WHERE entity_id = {id:UUID}", parameters={"id": entity_id},
        ))
        security_prior = _one(self.client.query(
            "SELECT issue_date, isin, cusip, figi, share_class, sector_id, "
            "sector_classification FROM ref.securities FINAL WHERE security_id = {id:UUID}",
            parameters={"id": security_id},
        ))
        listing_prior = _one(self.client.query(
            "SELECT first_traded, local_symbol, is_primary FROM ref.listings FINAL "
            "WHERE listing_id = {id:UUID}", parameters={"id": listing_id},
        ))
        first_seen = min(
            [value for value in (instrument.get("first_seen"),
                                 entity_prior[0] if entity_prior else None,
                                 listing_prior[0] if listing_prior else None)
             if value is not None], default=now.date(),
        )
        last_seen = max(now.date(), instrument.get("last_seen") or now.date(),
                        entity_prior[1] if entity_prior else now.date())
        active = bool(instrument.get("active", True))
        security_type = {"equity": "common"}.get(
            str(instrument["security_type"]), str(instrument["security_type"])
        )
        self._insert("ref.entities", {
            "entity_id": entity_id, "entity_type": "issuer",
            "legal_name": str(instrument.get("name") or
                              (entity_prior[2] if entity_prior else instrument["trading_symbol"])),
            "lei": None, "country_of_domicile": country, "country_of_incorp": country,
            "active": active, "first_seen": first_seen, "last_seen": last_seen,
            "version": version, "ingested_at": now,
        })
        self._insert("ref.securities", {
            "security_id": security_id, "entity_id": entity_id,
            "security_type": security_type,
            "isin": instrument.get("isin") or (security_prior[1] if security_prior else None),
            "cusip": security_prior[2] if security_prior else None,
            "figi": security_prior[3] if security_prior else None,
            "share_class": security_prior[4] if security_prior else "",
            "currency_code": currency,
            "issue_date": security_prior[0] if security_prior and security_prior[0] else first_seen,
            "maturity_date": None,
            "sector_id": security_prior[5] if security_prior else None,
            "sector_classification": security_prior[6] if security_prior else "",
            "active": active, "version": version, "ingested_at": now,
        })
        self._insert("ref.listings", {
            "listing_id": listing_id, "security_id": security_id,
            "exchange_code": exchange, "country_code": country,
            "trading_symbol": str(instrument["trading_symbol"]),
            "local_symbol": instrument.get("local_symbol") or
                            (listing_prior[1] if listing_prior else None),
            "mic": exchange_row[0],
            "lot_size": int(instrument.get("lot_size") or 1),
            "tick_size": instrument.get("tick_size"),
            "is_primary": listing_prior[2] if listing_prior else True,
            "first_traded": listing_prior[0] if listing_prior and listing_prior[0] else first_seen,
            "last_traded": None if active else last_seen,
            "active": active, "version": version, "ingested_at": now,
        })
        alias_prior = _one(self.client.query(
            "SELECT minOrNull(valid_from) FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = {kind:String} AND alias_value = {value:String} "
            "AND target_kind = 'listing' AND target_id = {id:UUID}",
            parameters={"kind": alias_kind, "value": alias_value, "id": listing_id},
        ))
        self._insert("ref.identifier_aliases", {
            "alias_kind": alias_kind, "alias_value": alias_value,
            "scope_country": country, "scope_exchange": exchange,
            "target_kind": "listing", "target_id": listing_id,
            "source": source,
            "valid_from": alias_prior[0] if alias_prior and alias_prior[0] else first_seen,
            "valid_to": None,
            "confidence": "exact", "notes": "", "version": version,
            "ingested_at": now,
        })
        return entity_id, security_id, listing_id

    def upsert_future(
        self,
        contract: Mapping[str, Any],
        *,
        underlying_listing_id: UUID,
        source: str,
        observed_at: datetime | None = None,
    ) -> UUID:
        """Store a future only when its underlying listing already exists."""
        now = observed_at or datetime.now(UTC)
        listing = _one(self.client.query(
            "SELECT exchange_code, country_code FROM ref.listings FINAL "
            "WHERE listing_id = {listing_id:UUID}",
            parameters={"listing_id": underlying_listing_id},
        ))
        if listing is None:
            raise UnresolvedReference(f"underlying listing unresolved: {underlying_listing_id}")
        exchange = str(contract["exchange_code"])
        country = str(contract["country_code"])
        if _decoded_text(listing[0]) != exchange or _decoded_text(listing[1]) != country:
            raise UnresolvedReference(f"future exchange/country mismatch: {exchange}")
        expiry = contract["expiry"]
        if not isinstance(expiry, date):
            raise TypeError("expiry must be a date")
        key = str(contract["contract_key"])
        canonical_id = contract_id(key)
        version = _version(now)
        self._insert("ref.contracts", {
            "contract_id": canonical_id, "underlying_listing_id": underlying_listing_id,
            "exchange_code": exchange, "country_code": country,
            "contract_type": "future", "expiry": expiry, "strike": None,
            "right": "", "exercise_style": "", "multiplier": int(contract.get("multiplier") or 1),
            "lot_size": int(contract.get("lot_size") or 1),
            "tick_size": contract.get("tick_size"), "weekly": bool(contract.get("weekly", False)),
            "active": bool(contract.get("active", True)),
            "version": version, "ingested_at": now,
        })
        alias_prior = _one(self.client.query(
            "SELECT minOrNull(valid_from) FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = 'upstox_instrument_key' AND alias_value = {value:String} "
            "AND target_kind = 'contract' AND target_id = {id:UUID}",
            parameters={"value": key, "id": canonical_id},
        ))
        self._insert("ref.identifier_aliases", {
            "alias_kind": "upstox_instrument_key", "alias_value": key,
            "scope_country": country, "scope_exchange": exchange,
            "target_kind": "contract", "target_id": canonical_id,
            "source": source,
            "valid_from": alias_prior[0] if alias_prior and alias_prior[0] else now.date(),
            "valid_to": None,
            "confidence": "exact", "notes": "", "version": version,
            "ingested_at": now,
        })
        return canonical_id
