"""ClickHouse v2 implementations of the dataset sinks and read ports (docs/architecture/07 §7).

One :class:`ClickHouseSinks` object serves every catalogued dataset for one
market. It delegates raw archiving and ingestion runs to the existing v2
storage class (``V2IndiaStorage`` / ``V2USStorage``) and reuses
``V2ReferenceWriter`` for reference rows, but it never branches on the
provider: ``source`` and ``source_channel`` come only from ``Provenance``.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from factorlab.ingest.datasets import (
    BarRecord,
    ConstituentRecord,
    ContractBarRecord,
    ContractRecord,
    InstrumentRecord,
    InstrumentRef,
    WriteResult,
)
from factorlab.ingest.datasets.ports import ArchivedCapture
from factorlab.ingest.datasets.reference import REFERENCE_MODES, ReferenceMode
from factorlab.ingest.provider import Provenance, RawCapture
from factorlab.ingest.universe_snapshot import diff_snapshot
from factorlab.storage.canonical_ids import natural_contract_id, natural_listing_ids
from factorlab.storage.clickhouse import _decimal, _decoded_text, _version
from factorlab.storage.sinks.calendar import canonical_bar_time, session_for, trade_date_for
from factorlab.storage.sinks.fundamentals import FundamentalsSinkMixin
from factorlab.storage.sinks.identity import (
    ContractIdentity,
    IdentityResolver,
    ListingIdentity,
)
from factorlab.storage.sinks.political import PoliticalSinkMixin
from factorlab.storage.v2_reference import UnresolvedReference, V2ReferenceWriter

_BAR_TABLES = {"market.bars": "listing_id", "market.futures_contract_bars": "contract_id"}


def _price(value: Decimal | None, scale: int = 6) -> Decimal | None:
    return None if value is None else _decimal(value, scale)


def _check_mode(mode: str) -> None:
    if mode not in REFERENCE_MODES:
        raise ValueError(f"unknown reference write mode {mode!r}")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ClickHouseSinks(PoliticalSinkMixin, FundamentalsSinkMixin):
    """Dataset sinks + ``ReferenceReader`` / ``CheckpointStore`` / ``RawArchiveReader``."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage
        self.client = storage.client
        self.references = V2ReferenceWriter(self.client)

    @classmethod
    def from_environment(cls, market: str) -> ClickHouseSinks:
        from factorlab.storage.v2_broker import V2BrokerStorage
        from factorlab.storage.v2_india import V2IndiaStorage

        # V2BrokerStorage extends V2USStorage with the broker.* writers.
        storage_cls = {"IND": V2IndiaStorage, "USA": V2BrokerStorage}.get(market)
        if storage_cls is None:
            raise ValueError(f"no ClickHouse v2 storage for market {market!r}")
        return cls(storage_cls.from_environment())

    def close(self) -> None:
        self.storage.close()

    # -- ProviderStorage (delegated) ------------------------------------------
    def archive_raw(self, capture: RawCapture, *, source: str, source_channel: str) -> UUID:
        return self.storage.archive_raw(capture, source=source, source_channel=source_channel)

    def start_ingestion_run(self, **kwargs: Any) -> Any:
        return self.storage.start_ingestion_run(**kwargs)

    def finish_ingestion_run(self, handle: Any, **kwargs: Any) -> None:
        self.storage.finish_ingestion_run(handle, **kwargs)

    # -- shared guarantees -------------------------------------------------------
    def _check_lineage(self, provenance: Provenance) -> None:
        active = getattr(self.storage, "_active_run_id", None)
        if active is None:
            raise RuntimeError("start an ingestion run before writing v2 rows")
        if provenance.ingest_run_id != active:
            raise RuntimeError(
                f"provenance run {provenance.ingest_run_id} is not the active run {active}"
            )

    def _insert(self, table: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        columns = list(records[0])
        self.client.insert(table, [[row[column] for column in columns] for row in records],
                           column_names=columns)

    def _park(self, provenance: Provenance, refs: Sequence[InstrumentRef], reason: str) -> None:
        now = datetime.now(UTC)
        self._insert("meta.unresolved_entities", [{
            "first_seen": now, "last_seen": now, "source": provenance.source,
            "alias_kind": ref.alias_kind or "natural_key", "alias_value": ref.label(),
            "scope_country": ref.country_code, "scope_exchange": ref.exchange_code or None,
            "context_json": json.dumps({
                "raw_id": str(provenance.raw_id) if provenance.raw_id else None,
                "source_channel": provenance.source_channel,
                "trading_symbol": ref.trading_symbol, "isin": ref.isin,
            }, sort_keys=True),
            "occurrence_count": 1, "retry_count": 0, "last_retry_at": now,
            "resolved_at": None, "resolved_target_kind": None, "resolved_target_id": None,
            "resolved_by": None, "resolution_note": reason,
            "version": _version(now), "ingested_at": now,
        } for ref in dict.fromkeys(refs)])

    # -- ref.listings ----------------------------------------------------------
    def upsert_instruments(self, rows: Sequence[InstrumentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)
        _check_mode(mode)
        resolver = IdentityResolver(self.client)
        known = resolver.listings(row.ref for row in rows)
        written = resolved = 0
        parked: dict[str, list[InstrumentRef]] = {}
        for row in rows:
            ref = row.ref
            identity = known.get(ref)
            if identity is None and mode != "authoritative":
                parked.setdefault(f"no listing ({mode} mode cannot mint)", []).append(ref)
                continue
            if identity is not None:
                resolved += 1
            if mode == "resolve_only":
                continue
            if mode == "alias_only":
                self.references.attach_alias(
                    target_kind="listing", target_id=identity.listing_id,
                    alias_kind=ref.alias_kind, alias_value=ref.alias_value,
                    scope_country=ref.country_code, scope_exchange=ref.exchange_code or None,
                    source=provenance.source, confidence=identity.confidence)
                written += 1
                continue
            natural = natural_listing_ids(exchange_code=ref.exchange_code,
                                          trading_symbol=ref.trading_symbol, isin=ref.isin)
            if identity is not None:
                ids = (identity.entity_id or natural[0], identity.security_id,
                       identity.listing_id)
                confidence = identity.confidence
            else:
                ids, confidence = natural, "exact"
            try:
                self.references.upsert_listing({
                    "instrument_key": ref.alias_value, "isin": ref.isin,
                    "country_code": ref.country_code, "exchange_code": ref.exchange_code,
                    "currency_code": row.currency, "trading_symbol": ref.trading_symbol,
                    "name": row.name, "security_type": row.product_type,
                    "lot_size": row.lot_size, "tick_size": _price(row.tick_size),
                    "active": row.active,
                    # the writer keeps the earliest of this and what it already knows
                    "first_seen": row.first_traded,
                }, alias_kind=ref.alias_kind, alias_value=ref.alias_value,
                    source=provenance.source, ids=ids, confidence=confidence)
            except UnresolvedReference as exc:
                parked.setdefault(str(exc), []).append(ref)
                continue
            written += 1
        for reason, refs in parked.items():
            self._park(provenance, refs, reason)
        return WriteResult(written, sum(len(refs) for refs in parked.values()), resolved)

    # -- ref.contracts ---------------------------------------------------------
    def upsert_contracts(self, rows: Sequence[ContractRecord], *, provenance: Provenance,
                         mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)
        _check_mode(mode)
        resolver = IdentityResolver(self.client)
        underlyings = resolver.listings(row.underlying for row in rows)
        known = resolver.contracts(row.ref for row in rows)
        written = resolved = 0
        parked: dict[str, list[InstrumentRef]] = {}
        for row in rows:
            if row.product_type == "option":
                parked.setdefault("options are not supported by this sink yet", []).append(row.ref)
                continue
            underlying = underlyings.get(row.underlying)
            if underlying is None:
                parked.setdefault("underlying listing unresolved", []).append(row.ref)
                continue
            contract = known.get(row.ref)
            contract_id = contract.contract_id if contract else resolver.natural_future(
                underlying_listing_id=underlying.listing_id, expiry=row.expiry)
            if contract_id is not None:
                resolved += 1
            elif mode == "authoritative":
                contract_id = natural_contract_id(
                    underlying_listing_id=underlying.listing_id, contract_type="future",
                    expiry=row.expiry, right=None, strike=None)
            else:
                parked.setdefault(f"no contract ({mode} mode cannot mint)", []).append(row.ref)
                continue
            if mode == "resolve_only":
                continue
            if mode == "alias_only":
                self.references.attach_alias(
                    target_kind="contract", target_id=contract_id,
                    alias_kind=row.ref.alias_kind, alias_value=row.ref.alias_value,
                    scope_country=row.ref.country_code,
                    scope_exchange=row.ref.exchange_code or None,
                    source=provenance.source, confidence="exact" if contract else "high")
                written += 1
                continue
            try:
                self.references.upsert_future({
                    "contract_key": row.ref.alias_value, "exchange_code": row.ref.exchange_code,
                    "country_code": row.ref.country_code, "expiry": row.expiry,
                    "lot_size": row.lot_size, "tick_size": _price(row.tick_size),
                    "multiplier": row.multiplier, "weekly": row.weekly, "active": row.active,
                }, underlying_listing_id=underlying.listing_id, source=provenance.source,
                    canonical_id=contract_id, alias_kind=row.ref.alias_kind)
            except UnresolvedReference as exc:
                parked.setdefault(str(exc), []).append(row.ref)
                continue
            written += 1
        for reason, refs in parked.items():
            self._park(provenance, refs, reason)
        return WriteResult(written, sum(len(refs) for refs in parked.values()), resolved)

    # -- ref.universe_membership ------------------------------------------------
    def _open_memberships(self, universe_code: str) -> dict[UUID, date]:
        rows = self.client.query(
            "SELECT listing_id, effective_from FROM ref.universe_membership FINAL "
            "WHERE universe_id = {universe:String} AND effective_to IS NULL",
            parameters={"universe": universe_code},
        ).result_rows
        return {row[0]: row[1] for row in rows}

    def _ensure_universe(self, universe_code: str, name: str, country: str,
                         provenance: Provenance) -> None:
        exists = self.client.query(
            "SELECT count() FROM ref.universes FINAL WHERE universe_id = {universe:String}",
            parameters={"universe": universe_code},
        ).result_rows[0][0]
        if exists:
            return
        now = datetime.now(UTC)
        self._insert("ref.universes", [{
            "universe_id": universe_code, "name": name or universe_code,
            "provider": provenance.source, "rebalance_freq": "continuous",
            "country_scope": [country], "active": True, "version": _version(now),
            "ingested_at": now,
        }])

    def write_constituents(self, rows: Sequence[ConstituentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult:
        """Each universe in ``rows`` is a complete snapshot as of ``provenance.as_of_time``."""
        self._check_lineage(provenance)
        _check_mode(mode)
        identities = IdentityResolver(self.client).listings(row.instrument for row in rows)
        snapshots: dict[str, dict[UUID, ConstituentRecord]] = {}
        unresolved: list[InstrumentRef] = []
        for row in rows:
            identity = identities.get(row.instrument)
            members = snapshots.setdefault(row.universe_code, {})
            if identity is None:
                unresolved.append(row.instrument)
                continue
            members[identity.listing_id] = row
        if unresolved:
            self._park(provenance, unresolved, "ref.universe_membership: constituent unresolved")
        resolved = sum(len(members) for members in snapshots.values())
        if mode != "authoritative":
            return WriteResult(0, len(unresolved), resolved)
        as_of = provenance.as_of_time.astimezone(UTC).date()
        now = datetime.now(UTC)
        written = 0
        for code, members in snapshots.items():
            current = self._open_memberships(code)
            diff = diff_snapshot(code, current, set(members), as_of=as_of)
            if not diff.unchanged:
                sample = next(iter(members.values()))
                self._ensure_universe(code, sample.universe_name,
                                      sample.instrument.country_code, provenance)
            base = {"universe_id": code, "source": provenance.source,
                    "raw_id": provenance.raw_id, "ingested_at": now}
            records = [{
                **base, "listing_id": listing,
                "weight": float(members[listing].weight) if members[listing].weight else None,
                "effective_from": diff.effective_from, "effective_to": None,
                "reason": "snapshot_add", "version": _version(now),
            } for listing in diff.add] + [{
                **base, "listing_id": listing, "weight": None,
                "effective_from": since, "effective_to": diff.effective_to,
                "reason": "snapshot_remove", "version": _version(now),
            } for listing, since in diff.close]
            self._insert("ref.universe_membership", records)
            written += len(records)
        return WriteResult(written, len(unresolved), resolved)

    def universe_members(self, universe_codes: Sequence[str], *,
                         as_of: date | None = None) -> list[UUID]:
        if not universe_codes:
            return []
        rows = self.client.query(
            "SELECT DISTINCT listing_id FROM ref.universe_membership FINAL "
            "WHERE universe_id IN {codes:Array(String)} AND effective_from <= {day:Date} "
            "AND (effective_to IS NULL OR effective_to >= {day:Date})",
            parameters={"codes": list(universe_codes),
                        "day": as_of or datetime.now(UTC).date()},
        ).result_rows
        return sorted((row[0] for row in rows), key=str)

    # -- ref.source_priorities (06 §13.1, 07 §10) -------------------------------------
    def sync_source_priorities(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Upsert one row per (dataset, country, resolution, source); rows come from bindings."""
        now = datetime.now(UTC)
        records = [{
            "dataset": row["dataset"], "country_code": row["country_code"],
            "resolution": row.get("resolution") or "", "source": row["source"],
            "priority": int(row["priority"]), "role": row["role"],
            "active": row["role"] in ("primary", "secondary"), "synced_at": now,
            "version": _version(now), "ingested_at": now,
        } for row in rows]
        self._insert("ref.source_priorities", records)
        return len(records)

    # -- broker.snapshot ----------------------------------------------------------
    _BROKER_WRITERS: ClassVar[dict[str, str]] = {
        "PositionSnapshot": "write_positions",
        "AccountStateRow": "write_account_state",
        "ExecutionRecord": "write_executions",
        "OpenOrderSnapshot": "write_open_orders",
    }

    def write_snapshot(self, rows: Sequence[Any], *, provenance: Provenance) -> WriteResult:
        """Route each broker shape to its ``V2BrokerStorage`` writer (identity + lineage there)."""
        self._check_lineage(provenance)
        groups: dict[str, list[Any]] = {}
        for row in rows:
            name = type(row).__name__
            if name not in self._BROKER_WRITERS:
                raise TypeError(f"not a broker record: {name}")
            groups.setdefault(name, []).append(row)
        written = 0
        for name, group in groups.items():
            writer = getattr(self.storage, self._BROKER_WRITERS[name], None)
            if writer is None:
                raise RuntimeError("broker rows need a V2BrokerStorage-backed sink")
            written += int(writer(group, provenance=provenance))
        return WriteResult(written, 0, written)

    # -- market.bars -----------------------------------------------------------
    def _bar_columns(self, row: BarRecord | ContractBarRecord, country: str,
                     provenance: Provenance, now: datetime) -> dict[str, Any]:
        return {
            "resolution": row.resolution,
            "session": row.session or session_for(country, row.bar_time, row.resolution),
            "bar_time": canonical_bar_time(country, row.bar_time, row.resolution),
            "trade_date": trade_date_for(country, row.bar_time, row.resolution),
            "open": _price(row.open), "high": _price(row.high),
            "low": _price(row.low), "close": _price(row.close),
            "volume": row.volume, "oi": row.oi,
            "source": provenance.source, "raw_id": provenance.raw_id,
            "ingest_run_id": provenance.ingest_run_id,
            "as_of_time": provenance.as_of_time, "ingested_at": provenance.ingested_at,
            "version": _version(now),
        }

    def write_bars(self, rows: Sequence[BarRecord], *, provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)
        identities = IdentityResolver(self.client).listings(row.instrument for row in rows)
        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []
        unresolved: list[InstrumentRef] = []
        for row in rows:
            identity: ListingIdentity | None = identities.get(row.instrument)
            if identity is None:
                unresolved.append(row.instrument)
                continue
            records.append({
                "country_code": identity.country_code, "listing_id": identity.listing_id,
                "security_id": identity.security_id, "entity_id": identity.entity_id,
                "product_type": identity.product_type,
                **self._bar_columns(row, identity.country_code, provenance, now),
                "turnover": _price(row.turnover, 4), "trades_count": row.trades_count,
                "settlement_price": _price(row.settlement_price),
                "source_channel": provenance.source_channel, "latency_ms": None,
            })
        self._insert("market.bars", records)
        if unresolved:
            self._park(provenance, unresolved, "market.bars: listing unresolved")
        return WriteResult(len(records), len(unresolved), len(records))

    def write_contract_bars(self, rows: Sequence[ContractBarRecord], *,
                            provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)
        identities = IdentityResolver(self.client).contracts(row.contract for row in rows)
        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []
        unresolved: list[InstrumentRef] = []
        for row in rows:
            identity: ContractIdentity | None = identities.get(row.contract)
            if identity is None:
                unresolved.append(row.contract)
                continue
            records.append({
                "country_code": identity.country_code,
                "underlying_listing_id": identity.underlying_listing_id,
                "contract_id": identity.contract_id,
                "source_symbol": row.contract.trading_symbol,
                **self._bar_columns(row, identity.country_code, provenance, now),
            })
        self._insert("market.futures_contract_bars", records)
        if unresolved:
            self._park(provenance, unresolved, "market.futures_contract_bars: contract unresolved")
        return WriteResult(len(records), len(unresolved), len(records))

    # -- read ports --------------------------------------------------------------
    def aliases_for(self, listing_ids: Sequence[UUID], *,
                    alias_kind: str) -> Mapping[UUID, InstrumentRef]:
        if not listing_ids:
            return {}
        rows = self.client.query(
            "SELECT a.target_id, a.alias_value, a.valid_from, l.exchange_code, "
            "l.trading_symbol, l.country_code, s.isin "
            "FROM ref.identifier_aliases AS a FINAL "
            "INNER JOIN ref.listings AS l FINAL ON l.listing_id = a.target_id "
            "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
            "WHERE a.alias_kind = {kind:String} AND a.target_kind = 'listing' "
            "AND a.valid_to IS NULL AND a.target_id IN {ids:Array(UUID)}",
            parameters={"kind": alias_kind, "ids": list(listing_ids)},
        ).result_rows
        best: dict[UUID, tuple[date, InstrumentRef]] = {}
        for target, value, valid_from, exchange, symbol, country, isin in rows:
            ref = InstrumentRef(
                alias_kind=alias_kind, alias_value=_decoded_text(value),
                exchange_code=_decoded_text(exchange), trading_symbol=_decoded_text(symbol),
                country_code=_decoded_text(country), isin=_decoded_text(isin) if isin else None,
            )
            since = valid_from or date.min
            if target not in best or since > best[target][0]:
                best[target] = (since, ref)
        return {target: ref for target, (_, ref) in best.items()}

    def active_listings(self, exchange_code: str) -> list[UUID]:
        rows = self.client.query(
            "SELECT listing_id FROM ref.listings FINAL "
            "WHERE exchange_code = {exchange:String} AND active",
            parameters={"exchange": exchange_code},
        ).result_rows
        return sorted((row[0] for row in rows), key=str)

    def natural_refs(self, listing_ids: Sequence[UUID]) -> Mapping[UUID, InstrumentRef]:
        if not listing_ids:
            return {}
        rows = self.client.query(
            "SELECT l.listing_id, l.exchange_code, l.trading_symbol, l.country_code, s.isin "
            "FROM ref.listings AS l FINAL "
            "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
            "WHERE l.listing_id IN {ids:Array(UUID)}",
            parameters={"ids": list(listing_ids)},
        ).result_rows
        return {row[0]: InstrumentRef("", "", _decoded_text(row[1]), _decoded_text(row[2]),
                                      _decoded_text(row[3]),
                                      isin=_decoded_text(row[4]) if row[4] else None)
                for row in rows}

    def watermarks(self, listing_ids: Sequence[UUID], *, dataset: str, source: str,
                   resolution: str) -> Mapping[UUID, datetime]:
        key = _BAR_TABLES.get(dataset)
        if key is None:
            raise ValueError(f"no watermark for dataset {dataset!r}")
        if not listing_ids:
            return {}
        # max(bar_time) is identical with or without FINAL: versions never move bar_time.
        rows = self.client.query(
            f"SELECT {key}, max(bar_time) FROM {dataset} "
            "WHERE resolution = {resolution:String} AND source = {source:String} "
            f"AND {key} IN {{ids:Array(UUID)}} GROUP BY {key}",
            parameters={"resolution": resolution, "source": source, "ids": list(listing_ids)},
        ).result_rows
        return {row[0]: _utc(row[1]) for row in rows}

    def load_raw(self, raw_id: UUID) -> ArchivedCapture:
        rows = self.client.query(
            "SELECT source, source_channel, transport, source_url, request_key, status_code, "
            "response_headers, response_body, content_type, content_encoding, fetched_at, "
            "metadata_json FROM raw.archive WHERE raw_id = {id:UUID} LIMIT 1",
            parameters={"id": raw_id},
        ).result_rows
        if not rows:
            raise KeyError(f"raw_id {raw_id} is not in raw.archive")
        (source, channel, transport, url, request_key, status, headers, body,
         content_type, encoding, fetched_at, metadata) = rows[0]
        body = body if isinstance(body, bytes) else str(body).encode("latin-1")
        if _decoded_text(encoding) == "gzip":
            body = gzip.decompress(body)
        return ArchivedCapture(
            raw_id=raw_id, source=_decoded_text(source), source_channel=_decoded_text(channel),
            capture=RawCapture(
                body=body, request_key=_decoded_text(request_key),
                transport=_decoded_text(transport),  # type: ignore[arg-type]
                fetched_at=_utc(fetched_at), source_url=_decoded_text(url),
                content_type=_decoded_text(content_type), status_code=status,
                headers=json.loads(headers or "{}"), metadata=json.loads(metadata or "{}"),
            ),
        )


__all__ = ["ClickHouseSinks"]
