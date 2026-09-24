"""Canonical v2 writer for the ``broker.*`` mirror tables (schema-rehaul §9, Wave 7).

Adapters hand over normalized broker facts (:mod:`factorlab.sources.ibkr.shapes`)
plus a :class:`~factorlab.shared.ingest.provider.Provenance`. This layer owns
every other column:

* identity — vendor conid -> ``listing_id``/``security_id``/``entity_id`` or
  ``contract_id`` through ``ref.identifier_aliases`` (``alias_kind='ibkr_conid'``).
  Misses are written as ``resolution_confidence='unresolved'`` and surfaced in
  ``meta.unresolved_entities``; this writer never creates reference data;
* enrichment — ``metric_canonical`` from ``ref.broker_metrics_map`` and
  ``execution_method_id`` from ``ref.execution_methods``;
* lineage — provenance columns plus a monotonic ``version``.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NamedTuple

from factorlab.shared.ingest.provider import Provenance
from factorlab.storage.clickhouse import _decimal, _version
from factorlab.storage.v2_us import V2USStorage

BROKER_ALIAS_KIND = {"ibkr": "ibkr_conid"}
MANUAL_EXECUTION_METHOD = "manual_gui_or_mobile"
UNMAPPED_EXECUTION_METHOD = "unmapped_api_client"

POSITIONS_TABLE = "broker.positions_snapshot"
ACCOUNT_STATE_TABLE = "broker.account_state_snapshot"
EXECUTIONS_TABLE = "broker.executions"
OPEN_ORDERS_TABLE = "broker.open_orders_snapshot"

# Identity columns present in each table's Wave 7 DDL (only positions carry entity_id).
_IDENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    POSITIONS_TABLE: ("listing_id", "security_id", "contract_id", "entity_id",
                      "resolution_confidence"),
    EXECUTIONS_TABLE: ("listing_id", "security_id", "contract_id", "resolution_confidence"),
    OPEN_ORDERS_TABLE: ("listing_id", "security_id", "contract_id", "resolution_confidence"),
}

# Decimal scale per column, from the Wave 7 DDL.
_SCALES = {
    "position": 6, "avg_cost": 6, "market_price": 6, "market_value": 6,
    "unrealized_pnl": 6, "realized_pnl_ytd": 6, "market_value_usd": 6,
    "value_num": 6, "quantity": 6, "price": 6, "commission": 6, "realized_pnl": 6,
    "filled_quantity": 6, "remaining_quantity": 6, "limit_price": 6, "aux_price": 6,
}


class Identity(NamedTuple):
    listing_id: uuid.UUID | None = None
    security_id: uuid.UUID | None = None
    contract_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    resolution_confidence: str = "unresolved"

    def columns(self, table: str) -> dict[str, Any]:
        values = self._asdict()
        return {name: values[name] for name in _IDENTITY_COLUMNS[table]}


UNRESOLVED = Identity()


def _fact_columns(row: Any) -> dict[str, Any]:
    columns = {field.name: getattr(row, field.name) for field in dataclasses.fields(row)}
    for name, scale in _SCALES.items():
        if name in columns:
            columns[name] = _decimal(columns[name], scale)
    return columns


class V2BrokerStorage(V2USStorage):
    """Writes broker mirror rows for US-booked accounts."""

    def _check_lineage(self, provenance: Provenance) -> None:
        if self._active_run_id is None or provenance.ingest_run_id != self._active_run_id:
            raise RuntimeError("broker writes require provenance from the active ingestion run")

    def _write(self, table: str, rows: Sequence[Any], provenance: Provenance,
               extra: Callable[[Any], Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        self._check_lineage(provenance)
        lineage = provenance.columns()
        now = datetime.now(UTC)
        self._insert_dicts(table, [
            {**_fact_columns(row), **extra(row), **lineage, "version": _version(now)}
            for row in rows
        ])
        return len(rows)

    # -- identity -------------------------------------------------------------

    def resolve_vendor_ids(self, broker_code: str, vendor_ids: Iterable[str], *,
                           raw_id: uuid.UUID | None) -> dict[str, Identity]:
        """Resolve broker instrument ids through approved aliases (read-only)."""
        alias_kind = BROKER_ALIAS_KIND[broker_code]
        values = sorted({value for value in vendor_ids if value})
        if not values:
            return {}
        aliases: dict[str, tuple[str, uuid.UUID, str]] = {}
        result = self.client.query(
            "SELECT alias_value, target_kind, target_id, toString(confidence) "
            "FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = {kind:String} AND alias_value IN {values:Array(String)} "
            "AND target_kind IN ('listing', 'contract') AND valid_from <= today() "
            "AND (valid_to IS NULL OR valid_to >= today()) "
            "ORDER BY alias_value, valid_from DESC",
            parameters={"kind": alias_kind, "values": values},
        )
        for alias_value, target_kind, target_id, confidence in result.result_rows:
            aliases.setdefault(str(alias_value), (str(target_kind), target_id, str(confidence)))

        listing_ids = [target for kind, target, _ in aliases.values() if kind == "listing"]
        listings: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
        if listing_ids:
            result = self.client.query(
                "SELECT l.listing_id, l.security_id, s.entity_id "
                "FROM ref.listings AS l FINAL INNER JOIN ref.securities AS s FINAL "
                "ON s.security_id = l.security_id "
                "WHERE l.listing_id IN {ids:Array(UUID)}",
                parameters={"ids": listing_ids},
            )
            listings = {row[0]: (row[1], row[2]) for row in result.result_rows}

        resolved: dict[str, Identity] = {}
        for value in values:
            alias = aliases.get(value)
            if alias is None:
                resolved[value] = UNRESOLVED
                self._identity_status(source=broker_code, alias_kind=alias_kind,
                                      alias_value=value, raw_id=raw_id,
                                      reason=f"no active {alias_kind} alias")
                continue
            target_kind, target_id, confidence = alias
            if target_kind == "listing":
                security_id, entity_id = listings.get(target_id, (None, None))
                identity = Identity(listing_id=target_id, security_id=security_id,
                                    entity_id=entity_id, resolution_confidence=confidence)
            else:
                identity = Identity(contract_id=target_id, resolution_confidence=confidence)
            resolved[value] = identity
            self._identity_status(source=broker_code, alias_kind=alias_kind, alias_value=value,
                                  raw_id=raw_id, target_kind=target_kind, target_id=target_id)
        return resolved

    def _identities(self, rows: Sequence[Any], provenance: Provenance) -> dict[str, Identity]:
        if not rows:
            return {}
        broker_code = rows[0].broker_code
        return self.resolve_vendor_ids(broker_code, (row.vendor_id for row in rows),
                                       raw_id=provenance.raw_id)

    # -- enrichment -----------------------------------------------------------

    def canonical_metrics(self, broker_code: str) -> dict[str, str]:
        result = self.client.query(
            "SELECT vendor_metric, canonical_metric FROM ref.broker_metrics_map FINAL "
            "WHERE broker_code = {broker:String} AND active",
            parameters={"broker": broker_code},
        )
        return {str(vendor): str(canonical) for vendor, canonical in result.result_rows}

    def execution_methods(self) -> dict[int, str]:
        result = self.client.query(
            "SELECT api_client_id, method_id FROM ref.execution_methods FINAL "
            "WHERE category = 'api' AND active AND api_client_id IS NOT NULL "
            "AND valid_from <= today() AND (valid_to IS NULL OR valid_to >= today())"
        )
        return {int(client_id): str(method) for client_id, method in result.result_rows}

    @staticmethod
    def execution_method_for(client_id: int | None, methods: Mapping[int, str]) -> str:
        """Client id 0 (or none) is a TWS/mobile order; API ids map via ``ref.execution_methods``."""
        if not client_id:
            return MANUAL_EXECUTION_METHOD
        return methods.get(client_id, UNMAPPED_EXECUTION_METHOD)

    # -- writers --------------------------------------------------------------

    def write_positions(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        identities = self._identities(rows, provenance)
        return self._write(POSITIONS_TABLE, rows, provenance, lambda row: {
            **identities.get(row.vendor_id, UNRESOLVED).columns(POSITIONS_TABLE),
            # Not reported by portfolio(); populated once FX/margin pullers exist.
            "fx_rate_to_base": None, "fx_rate_source_time": None,
            "initial_margin_contribution": None, "maintenance_margin_contribution": None,
        })

    def write_account_state(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        canonical = self.canonical_metrics(rows[0].broker_code) if rows else {}
        return self._write(ACCOUNT_STATE_TABLE, rows, provenance, lambda row: {
            "metric_canonical": canonical.get(row.metric, ""),
        })

    def write_executions(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        identities = self._identities(rows, provenance)
        methods = self.execution_methods() if rows else {}
        return self._write(EXECUTIONS_TABLE, rows, provenance, lambda row: {
            **identities.get(row.vendor_id, UNRESOLVED).columns(EXECUTIONS_TABLE),
            "execution_method_id": self.execution_method_for(row.placed_by_client, methods),
            "strategy_id": None,  # populated by the trade engine
        })

    def write_open_orders(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        identities = self._identities(rows, provenance)
        methods = self.execution_methods() if rows else {}
        return self._write(OPEN_ORDERS_TABLE, rows, provenance, lambda row: {
            **identities.get(row.vendor_id, UNRESOLVED).columns(OPEN_ORDERS_TABLE),
            "execution_method_id": self.execution_method_for(row.placed_by_client, methods),
            "strategy_id": None,
        })
