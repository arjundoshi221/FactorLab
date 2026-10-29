"""Read-only canonical instrument inventory and explicitly scoped dataset inspection."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from factorlab.clickhouse import ClickHouse
from factorlab.components.api.catalog_pipelines import PIPELINES
from factorlab.components.api.catalog_query import (
    CatalogError,
    ColumnInfo,
    QuerySlots,
    TableInfo,
    clean_row,
    ensure_preview_allowed,
    max_window_width,
    query_settings,
    translate_database_error,
    visible_columns,
)
from factorlab.components.api.catalog_text import table_text
from factorlab.components.api.schema_map import _planned_v2_tables, _rows
from factorlab.storage.identity_repair import RETIRED_LISTINGS

Kind = Literal["listing", "contract"]
State = Literal["stored", "empty", "missing_table", "unavailable", "unsupported"]
SLOTS = QuerySlots(3)


@lru_cache
def registry() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("instrument_datasets.json").read_text())


class Instrument(BaseModel):
    kind: Kind
    id: UUID
    listing_id: UUID
    security_id: UUID
    entity_id: UUID
    symbol: str
    name: str
    country: str
    exchange: str
    instrument_type: str
    active: bool
    configured: bool
    expiry: date | None = None
    strike: str | None = None
    right: str = ""
    sector_id: str = ""
    sector: str = ""


class InstrumentPage(BaseModel):
    items: list[Instrument]
    total: int
    limit: int
    offset: int


class SectorCount(BaseModel):
    sector_id: str
    sector: str
    count: int


class SectorSummary(BaseModel):
    items: list[SectorCount]
    unclassified: int
    total: int


class Dataset(BaseModel):
    table: str
    title: str
    description: str
    group: str
    classification: str
    relationships: list[str]
    time_column: str | None
    dimensions: list[str]
    writers: list[str]
    notes: list[str]


class Identity(BaseModel):
    instrument: Instrument
    aliases: list[dict[str, Any]]
    related: list[Instrument]
    datasets: list[Dataset]
    warnings: list[str] = Field(default_factory=list)
    identifiers: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class DatasetResult(BaseModel):
    table: str
    state: State
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str = ""
    groups: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    has_more: bool = False
    limit: int = 50
    offset: int = 0
    start: datetime | None = None
    end: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


def inventory_sql() -> str:
    # Membership is configuration, never inferred from stored bar counts. Listings retired
    # as duplicate identities are tombstones of a canonical listing, not instruments.
    return f"""SELECT 'listing' AS kind, l.listing_id AS id, l.listing_id AS listing_id,
        l.security_id AS security_id, s.entity_id AS entity_id, l.trading_symbol AS symbol,
        e.legal_name AS name, l.country_code AS country, l.exchange_code AS exchange,
        s.security_type AS instrument_type, l.active AS active,
        l.listing_id IN (SELECT listing_id FROM meta.expected_series FINAL
            WHERE active AND contract_id IS NULL) AS configured,
        CAST(NULL, 'Nullable(Date)') AS expiry, CAST(NULL, 'Nullable(String)') AS strike,
        '' AS right
        FROM ref.listings AS l FINAL
        LEFT JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
        LEFT JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
        WHERE l.listing_id NOT IN ({RETIRED_LISTINGS})
        UNION ALL
        SELECT 'contract', c.contract_id, c.underlying_listing_id, l.security_id,
        s.entity_id, l.trading_symbol, e.legal_name, c.country_code, c.exchange_code,
        c.contract_type, c.active,
        c.contract_id IN (SELECT contract_id FROM meta.expected_series FINAL WHERE active),
        c.expiry, toString(c.strike), c.right
        FROM ref.contracts AS c FINAL
        LEFT JOIN ref.listings AS l FINAL ON l.listing_id = c.underlying_listing_id
        LEFT JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
        LEFT JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
        WHERE c.underlying_listing_id NOT IN ({RETIRED_LISTINGS})"""


def current_sector_sql() -> str:
    # Current GICS sector per listing (ADR-0017); contracts inherit their underlying's.
    return """SELECT h.listing_id AS listing_id, any(h.sector_id) AS sector_id,
        any(n.name) AS sector
        FROM ref.security_sector_history AS h FINAL
        LEFT JOIN (SELECT sector_id, any(name) AS name FROM ref.sectors FINAL
            WHERE classification = 'gics' GROUP BY sector_id) AS n ON n.sector_id = h.sector_id
        WHERE h.classification = 'gics' AND h.effective_to IS NULL
        GROUP BY h.listing_id"""


def sectored_inventory_sql() -> str:
    # An unclassified listing has '' for both sector columns.
    return f"""SELECT i.*, ifNull(cs.sector_id, '') AS sector_id, ifNull(cs.sector, '') AS sector
        FROM ({inventory_sql()}) AS i
        LEFT JOIN ({current_sector_sql()}) AS cs ON cs.listing_id = i.listing_id"""


INVENTORY_WHERE = """WHERE ({search:String} = '' OR positionCaseInsensitiveUTF8(symbol, {search:String}) > 0
    OR positionCaseInsensitiveUTF8(name, {search:String}) > 0 OR toString(id) = {search:String}
    OR tuple(kind, id) IN (SELECT target_kind, target_id FROM ref.identifier_aliases FINAL
        WHERE positionCaseInsensitiveUTF8(alias_value, {search:String}) > 0)
    OR security_id IN (SELECT target_id FROM ref.identifier_aliases FINAL
        WHERE target_kind = 'security' AND positionCaseInsensitiveUTF8(alias_value, {search:String}) > 0)
    OR entity_id IN (SELECT target_id FROM ref.identifier_aliases FINAL
        WHERE target_kind = 'entity' AND positionCaseInsensitiveUTF8(alias_value, {search:String}) > 0))
    AND ({country:String} = '' OR country = {country:String})
    AND ({exchange:String} = '' OR exchange = {exchange:String})
    AND ({instrument_type:String} = '' OR instrument_type = {instrument_type:String})
    AND ({active:String} = 'all' OR active = ({active:String} = 'yes'))
    AND ({configured:String} = 'all' OR configured = ({configured:String} = 'yes'))
    AND ({kind:String} = 'all' OR kind = {kind:String})
    AND ({sector:String} = '' OR sector_id = if({sector:String} = 'none', '', {sector:String}))"""


def scope(dataset: dict[str, Any], kind: Kind) -> tuple[str, str]:
    rules = dataset["relationships"].get(kind, [])
    if not rules:
        return "0", "'unsupported'"
    predicate = " OR ".join(f"({rule})" for _, rule in rules)
    relationship = (
        "multiIf(" + ", ".join(f"({rule}), '{label}'" for label, rule in rules) + ", 'unsupported')"
    )
    return predicate, relationship


class InstrumentRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def query(
        self, sql: str, parameters: dict[str, Any], max_rows: int = 1000
    ) -> list[dict[str, Any]]:
        SLOTS.acquire()
        try:
            settings = query_settings("rows", max_rows=max_rows)
            # Partial aggregate results would falsely claim complete instrument coverage.
            settings["result_overflow_mode"] = "throw"
            return _rows(self.client.query(sql, parameters=parameters, settings=settings))
        except CatalogError:
            raise
        except Exception as exc:
            raise translate_database_error(exc) from exc
        finally:
            SLOTS.release()

    def inventory(
        self,
        search: str,
        country: str,
        exchange: str,
        instrument_type: str,
        active: str,
        configured: str,
        limit: int,
        offset: int,
        *,
        sector: str = "",
        kind: str = "all",
    ) -> InstrumentPage:
        params: dict[str, Any] = {
            "search": search,
            "country": country,
            "exchange": exchange,
            "instrument_type": instrument_type,
            "active": active,
            "configured": configured,
            "kind": kind,
            "sector": sector,
            "limit": limit,
            "offset": offset,
        }
        base = f"FROM ({sectored_inventory_sql()}) {INVENTORY_WHERE}"
        total = self.query(f"SELECT count() AS total {base}", params)[0]["total"]
        rows = self.query(
            f"SELECT * {base} ORDER BY symbol, exchange, kind, id LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}",
            params,
        )
        return InstrumentPage(
            items=[Instrument(**r) for r in rows], total=total, limit=limit, offset=offset
        )

    def sectors(self, search: str, country: str, active: str, configured: str) -> SectorSummary:
        """Listing counts per current GICS sector under the same filters as the inventory."""
        params: dict[str, Any] = {
            "search": search,
            "country": country,
            "exchange": "",
            "instrument_type": "",
            "active": active,
            "configured": configured,
            "kind": "listing",
            "sector": "",
        }
        rows = self.query(
            f"""SELECT sector_id, any(sector) AS sector, count() AS count
                FROM ({sectored_inventory_sql()}) {INVENTORY_WHERE}
                GROUP BY sector_id ORDER BY sector, sector_id""",
            params,
        )
        items = [SectorCount(**r) for r in rows if r["sector_id"]]
        unclassified = sum(int(r["count"]) for r in rows if not r["sector_id"])
        return SectorSummary(
            items=items,
            unclassified=unclassified,
            total=unclassified + sum(i.count for i in items),
        )

    def instrument(self, kind: Kind, id: UUID) -> Instrument:
        rows = self.query(
            f"SELECT * FROM ({inventory_sql()}) WHERE kind = {{kind:String}} AND id = {{id:UUID}} LIMIT 1",
            {"kind": kind, "id": id},
        )
        if not rows:
            raise HTTPException(404, "Instrument not found")
        return Instrument(**rows[0])

    def identity(self, kind: Kind, id: UUID) -> Identity:
        item = self.instrument(kind, id)
        warnings = []
        aliases = []
        related = []
        try:
            aliases = self.query(
                """SELECT alias_kind, alias_value, target_kind, target_id, source,
                scope_country, scope_exchange, valid_from, valid_to, confidence
                FROM ref.identifier_aliases FINAL WHERE tuple(target_kind, target_id) IN
                [('listing', {listing_id:UUID}), ('security', {security_id:UUID}),
                 ('entity', {entity_id:UUID}), ({kind:String}, {id:UUID})]
                ORDER BY target_kind, alias_kind, valid_from LIMIT 201""",
                item.model_dump(),
            )
            if len(aliases) > 200:
                warnings.append("Alias list limited to 200; inspect the alias dataset for more.")
        except CatalogError as exc:
            warnings.append(str(exc))
        try:
            related = self.query(
                f"SELECT * FROM ({inventory_sql()}) WHERE security_id = {{security_id:UUID}} AND NOT (kind = {{kind:String}} AND id = {{id:UUID}}) ORDER BY kind, symbol, id LIMIT 201",
                item.model_dump(),
            )
            if len(related) > 200:
                warnings.append("Related instrument list limited to 200.")
        except CatalogError as exc:
            warnings.append(str(exc))
        datasets = []
        for name, data in registry().items():
            description = table_text(name)
            notes = list(description.notes)
            if name == "market.bars":
                notes.append(
                    "Daily collection and historical recovery write the same physical table."
                )
            if name == "market.bars_best":
                notes.append(
                    "Virtual source-priority selection over market.bars; inspect market.bars and ref.source_priorities. Not additional physical storage."
                )
                notes.append(
                    "Each field uses the lowest numeric priority among active primary/secondary sources. Shadow and disabled sources are excluded."
                )
            if data["classification"] == "related_context":
                notes.append(
                    "Related context only; never an instrument total. Follow recorded provenance or inspect the catalog."
                )
            datasets.append(
                Dataset(
                    table=name,
                    title=description.title,
                    description=description.summary,
                    group=data["group"],
                    classification=data["classification"],
                    relationships=[r[0] for r in data["relationships"].get(kind, [])],
                    time_column=data["time_column"],
                    dimensions=data["dimensions"],
                    writers=[p.id for p in PIPELINES if name in p.tables_written],
                    notes=notes,
                )
            )
        identifiers = {}
        for table, column, value in [
            ("ref.listings", "listing_id", item.listing_id),
            ("ref.securities", "security_id", item.security_id),
            ("ref.entities", "entity_id", item.entity_id),
            *(([("ref.contracts", "contract_id", id)]) if kind == "contract" else []),
        ]:
            try:
                schema = next(t for t in _planned_v2_tables() if t.name == table)
                info = TableInfo(
                    name=table,
                    engine=schema.engine,
                    columns=tuple(ColumnInfo(c.name, c.type) for c in schema.columns),
                )
                ensure_preview_allowed(info)
                columns = visible_columns(info)
                records = self.query(
                    f"SELECT {', '.join(expr for _, expr in columns)} FROM {table} FINAL WHERE {column} = {{value:UUID}} LIMIT 1",
                    {"value": value},
                )
                names = [c for c, _ in columns]
                identifiers[table] = [
                    dict(
                        zip(
                            names,
                            clean_row([r.get(c) for c in names], names, table=table, limit=500)[0],
                            strict=True,
                        )
                    )
                    for r in records
                ]
            except CatalogError as exc:
                warnings.append(f"{table}: {exc}")
        return Identity(
            instrument=item,
            aliases=aliases[:200],
            related=[Instrument(**r) for r in related[:200]],
            datasets=datasets,
            warnings=warnings,
            identifiers=identifiers,
        )

    def dataset(
        self,
        kind: Kind,
        id: UUID,
        name: str,
        *,
        preview: bool = False,
        provider: str = "",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DatasetResult:
        if name not in registry():
            raise HTTPException(404, "Dataset not registered")
        data = registry()[name]
        predicate, relationship = scope(data, kind)
        result = DatasetResult(table=name, state="unsupported", limit=limit, offset=offset)
        item = self.instrument(kind, id)
        if predicate == "0":
            result.message = (
                "No reliable instrument linkage. Related context is not instrument coverage."
            )
            return result
        params = item.model_dump()
        try:
            exists = self.query(
                "SELECT name FROM system.tables WHERE database = {db:String} AND name = {table:String}",
                {"db": name.split(".")[0], "table": name.split(".")[1]},
            )
            if not exists:
                result.state = "missing_table"
                result.message = "Table is not present in the connected database."
                return result
            schema = next(t for t in _planned_v2_tables() if t.name == name)
            info = TableInfo(
                name=name,
                engine=data["engine"],
                columns=tuple(ColumnInfo(c.name, c.type) for c in schema.columns),
                time_column=data["time_column"],
            )
            ensure_preview_allowed(info)
            source = name + (" FINAL" if info.uses_final else "")
            where = f"({predicate})"
            if provider:
                if "source" not in data["columns"]:
                    raise HTTPException(422, "This dataset has no provider dimension")
                where += " AND source = {provider:String}"
                params["provider"] = provider
            if not preview:
                dims = data["dimensions"]
                event = (
                    f"minOrNull(`{info.time_column}`) AS earliest, maxOrNull(`{info.time_column}`) AS latest"
                    if info.time_column
                    else "NULL AS earliest, NULL AS latest"
                )
                ingestion = "maxOrNull(ingested_at)" if "ingested_at" in data["columns"] else "NULL"
                dimensions = ", " + ", ".join(f"`{c}`" for c in dims) if dims else ""
                result.groups = self.query(
                    f"SELECT {relationship} AS relationship{dimensions}, count() AS rows, {event}, {ingestion} AS latest_ingestion FROM {source} WHERE {where} GROUP BY relationship{dimensions} ORDER BY relationship{dimensions}",
                    params,
                    2000,
                )
                result.state = "stored" if result.groups else "empty"
                return result
            end = end or datetime.now(UTC)
            start = start or end - timedelta(days=30)
            start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
            end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
            width = max_window_width(name)
            if start >= end or end - start > width:
                raise HTTPException(
                    422, f"Use an increasing date window of at most {width.days} days"
                )
            if info.time_column:
                where += f" AND `{info.time_column}` >= {{start:DateTime64(3)}} AND `{info.time_column}` < {{end:DateTime64(3)}}"
                params.update(start=start, end=end)
                result.start, result.end = start, end
            columns = visible_columns(info)
            order = f"`{info.time_column}` DESC, " if info.time_column else ""
            order += data["sorting_key"] or columns[0][1]
            params.update(limit=limit + 1, offset=offset)
            records = self.query(
                f"SELECT {relationship} AS relationship, {', '.join(expr + ' AS `' + col + '`' for col, expr in columns)} FROM {source} WHERE {where} ORDER BY {order} LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}",
                params,
                limit + 1,
            )
            result.columns = ["relationship", *[c for c, _ in columns]]
            result.rows = [
                clean_row(
                    [r.get(c) for c in result.columns], result.columns, table=name, limit=500
                )[0]
                for r in records[:limit]
            ]
            result.has_more = len(records) > limit
            result.state = "stored" if records else "empty"
            result.evidence = self.provenance(records[:limit])
        except CatalogError as exc:
            result.state = "unavailable"
            result.message = str(exc)
        return result

    def provenance(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        accounts = list(
            {
                (r["broker_code"], r["account_id"])
                for r in records
                if r.get("broker_code") and r.get("account_id")
            }
        )
        if accounts:
            try:
                info = TableInfo(
                    name="ref.broker_account_owners", engine="ReplacingMergeTree", columns=()
                )
                ensure_preview_allowed(info)
                owners = self.query(
                    "SELECT broker_code, account_id, user_id, simulated FROM ref.broker_account_owners FINAL WHERE tuple(broker_code, account_id) IN {accounts:Array(Tuple(String, String))} LIMIT 1000",
                    {"accounts": accounts},
                )
                names = ["broker_code", "account_id", "user_id", "simulated"]
                evidence["account_context"] = {
                    "state": "stored" if owners else "unavailable",
                    "message": "Account ownership and simulation flags are related context, not instrument totals. Missing flags mean unknown simulation status.",
                    "table": info.name,
                    "columns": names,
                    "rows": [
                        clean_row([r.get(c) for c in names], names, table=info.name, limit=500)[0]
                        for r in owners
                    ],
                }
            except CatalogError as exc:
                evidence["account_context"] = {"state": "unavailable", "message": str(exc)}
        for field, table, key in [
            ("raw_id", "raw.archive", "raw_id"),
            ("ingest_run_id", "meta.ingestion_runs", "run_id"),
            ("computation_id", "derived.computations", "computation_id"),
        ]:
            ids = list({str(r[field]) for r in records if r.get(field)})
            if field == "raw_id":
                ids = list(
                    set(ids)
                    | {str(value) for r in records for value in r.get("source_raw_ids", [])}
                )
            if not ids:
                evidence[field] = {
                    "state": "unavailable",
                    "message": "No recorded identifier in this page.",
                }
                continue
            try:
                schema = next(t for t in _planned_v2_tables() if t.name == table)
                info = TableInfo(
                    name=table,
                    engine=schema.engine,
                    columns=tuple(ColumnInfo(c.name, c.type) for c in schema.columns),
                )
                ensure_preview_allowed(info)
                columns = visible_columns(info)
                rows = self.query(
                    f"SELECT {', '.join(expr + ' AS `' + col + '`' for col, expr in columns)} FROM {table}{' FINAL' if info.uses_final else ''} WHERE `{key}` IN {{ids:Array(UUID)}} LIMIT 201",
                    {"ids": ids},
                    201,
                )
                names = [c for c, _ in columns]
                found = {str(r[key]) for r in rows}
                evidence[field] = {
                    "state": "stored" if rows else "unavailable",
                    "table": table,
                    "columns": names,
                    "rows": [
                        clean_row([r.get(c) for c in names], names, table=table, limit=500)[0]
                        for r in rows
                    ],
                    "unavailable_ids": [id for id in ids if id not in found],
                }
            except CatalogError as exc:
                evidence[field] = {
                    "state": "unavailable",
                    "message": str(exc),
                    "unavailable_ids": ids,
                }
        return evidence


def repository() -> InstrumentRepository:
    return InstrumentRepository(ClickHouse.from_environment().client)


Repo = Annotated[InstrumentRepository, Depends(repository)]
router = APIRouter()


@router.get("/instruments", response_model=InstrumentPage)
def instruments(
    repo: Repo,
    search: Annotated[str, Query(max_length=200)] = "",
    country: Annotated[str, Query(max_length=2)] = "",
    exchange: Annotated[str, Query(max_length=50)] = "",
    instrument_type: Annotated[str, Query(max_length=50)] = "",
    active: Literal["all", "yes", "no"] = "all",
    configured: Literal["all", "yes", "no"] = "all",
    sector: Annotated[str, Query(max_length=20, pattern=r"^(none|[0-9]{0,8})$")] = "",
    kind: Literal["all", "listing", "contract"] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
) -> InstrumentPage:
    try:
        return repo.inventory(
            search.strip(),
            country,
            exchange,
            instrument_type,
            active,
            configured,
            limit,
            offset,
            sector=sector,
            kind=kind,
        )
    except CatalogError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/sectors", response_model=SectorSummary)
def sectors(
    repo: Repo,
    search: Annotated[str, Query(max_length=200)] = "",
    country: Annotated[str, Query(max_length=2)] = "US",
    active: Literal["all", "yes", "no"] = "all",
    configured: Literal["all", "yes", "no"] = "all",
) -> SectorSummary:
    try:
        return repo.sectors(search.strip(), country, active, configured)
    except CatalogError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/instruments/{kind}/{id}", response_model=Identity)
def identity(repo: Repo, kind: Kind, id: UUID) -> Identity:
    try:
        return repo.identity(kind, id)
    except CatalogError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/instruments/{kind}/{id}/datasets/{dataset}/coverage", response_model=DatasetResult)
def coverage(repo: Repo, kind: Kind, id: UUID, dataset: str) -> DatasetResult:
    try:
        return repo.dataset(kind, id, dataset)
    except CatalogError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/instruments/{kind}/{id}/datasets/{dataset}/rows", response_model=DatasetResult)
def rows(
    repo: Repo,
    kind: Kind,
    id: UUID,
    dataset: str,
    provider: Annotated[str, Query(max_length=100)] = "",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
) -> DatasetResult:
    try:
        return repo.dataset(
            kind,
            id,
            dataset,
            preview=True,
            provider=provider,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
    except CatalogError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
