"""ClickHouse sink for ``fundamentals.filings`` (docs/architecture/07 §7, P8).

Issuer resolution: ``cik`` alias -> entity; otherwise the ticker hint -> the
listing's security -> its issuer entity, after which the ``cik`` alias is
attached (confidence ``medium``) so later runs resolve directly. Filing ids are
minted here from the accession number (rule R5).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from factorlab.shared.ingest.datasets import EntityRef, WriteResult
from factorlab.shared.ingest.datasets.fundamentals import (
    FundamentalFilingRecord,
    FundamentalsRow,
    LineItemRecord,
)
from factorlab.shared.ingest.provider import Provenance
from factorlab.storage.canonical_ids import filing_id
from factorlab.storage.clickhouse import _decimal, _version
from factorlab.storage.sinks.identity import IdentityResolver

REGULATOR = {"cik": "sec"}


def unit_class(unit: str) -> tuple[str, str | None]:
    """XBRL unit -> (``unit`` column vocabulary, ISO currency or None)."""
    if len(unit) == 3 and unit.isalpha() and unit.isupper():
        return "currency", unit
    if "/" in unit:
        numerator, _, denominator = unit.partition("/")
        if denominator.lower() == "shares" and len(numerator) == 3:
            return "usd_per_share" if numerator == "USD" else "currency_per_share", numerator
        return "ratio", None
    return {"shares": "shares", "pure": "ratio"}.get(unit.lower(), "count"), None


class FundamentalsSinkMixin:
    """Mixed into ``ClickHouseSinks``; relies on ``client``, ``references``, ``_insert``."""

    client: Any

    def _issuers(self, rows: Sequence[FundamentalsRow],
                 provenance: Provenance) -> dict[EntityRef, UUID]:
        issuers = {row.issuer: row.issuer_hint for row in rows}
        by_alias: dict[EntityRef, UUID] = {}
        for kind in {ref.alias_kind for ref in issuers}:
            values = sorted({ref.alias_value for ref in issuers if ref.alias_kind == kind})
            for value, target in self.client.query(
                "SELECT alias_value, target_id FROM ref.identifier_aliases FINAL "
                "WHERE alias_kind = {kind:String} AND target_kind = 'entity' "
                "AND valid_to IS NULL AND alias_value IN {values:Array(String)}",
                parameters={"kind": kind, "values": values},
            ).result_rows:
                by_alias[next(r for r in issuers if r.alias_kind == kind
                              and r.alias_value == value)] = target
        hints = {ref: hint for ref, hint in issuers.items() if ref not in by_alias and hint}
        listings = IdentityResolver(self.client).listings(hints.values())
        for ref, hint in hints.items():
            identity = listings.get(hint)
            if identity is None or identity.entity_id is None:
                continue
            by_alias[ref] = identity.entity_id
            self.references.attach_alias(  # type: ignore[attr-defined]
                target_kind="entity", target_id=identity.entity_id, alias_kind=ref.alias_kind,
                alias_value=ref.alias_value, scope_country=hint.country_code,
                scope_exchange=None, source=provenance.source, confidence="medium")
        return by_alias

    def write_fundamentals(self, rows: Sequence[FundamentalsRow], *,
                           provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)  # type: ignore[attr-defined]
        entities = self._issuers(rows, provenance)
        now = datetime.now(UTC)
        filings, items, unresolved = [], [], []
        for row in rows:
            entity = entities.get(row.issuer)
            if entity is None:
                unresolved.append(row.issuer)
                continue
            regulator = REGULATOR.get(row.issuer.alias_kind, row.issuer.alias_kind)
            fid = filing_id(regulator, row.accession_number)
            base = {"filing_id": fid, "entity_id": entity, "source": provenance.source,
                    "raw_id": provenance.raw_id, "as_of_time": provenance.as_of_time,
                    "ingested_at": now, "version": _version(now)}
            if isinstance(row, FundamentalFilingRecord):
                # Company facts carry only the filing date; acceptance time is unknown.
                filed_at = datetime.combine(row.filing_date, time(0), tzinfo=UTC)
                filings.append({
                    **base, "form_type": row.form_type, "filing_date": row.filing_date,
                    "period_end": row.period_end, "period_type": row.period_type,
                    "fiscal_year": row.fiscal_year, "fiscal_period": row.fiscal_period,
                    "filed_at": filed_at, "accepted_at": filed_at,
                    "accession_number": row.accession_number, "amends_filing_id": None,
                    "original_filing_id": fid, "amendment_seq": 0, "is_amended": False,
                    "is_amendment": row.is_amendment, "filing_url": row.filing_url,
                })
            elif isinstance(row, LineItemRecord):
                unit, currency = unit_class(row.unit)
                items.append({
                    **base, "tag": f"{row.taxonomy}:{row.tag}", "tag_standard": row.taxonomy,
                    "statement": "unclassified", "period_end": row.period_end,
                    "period_start": row.period_start,
                    "period_type": "duration" if row.period_start else "point",
                    "value": _decimal(Decimal(row.value), 4), "currency_code": currency,
                    "unit": unit, "context_ref": row.frame or "",
                    "dimensions": json.dumps({"xbrl_unit": row.unit}, sort_keys=True),
                })
        self._insert("fundamentals.filings", filings)  # type: ignore[attr-defined]
        self._insert("fundamentals.line_items", items)  # type: ignore[attr-defined]
        if unresolved:
            self._park_entities(provenance, unresolved,  # type: ignore[attr-defined]
                                "issuer has no entity: add a cik alias or a ticker hint")
        written = len(filings) + len(items)
        return WriteResult(written, len(unresolved), written)


__all__ = ["FundamentalsSinkMixin", "unit_class"]
