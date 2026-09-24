"""FactorLab FastAPI application."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path as FileSystemPath
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from factorlab.api.auth import require_api_key
from factorlab.api.docker_images import DockerImagesResponse, read_docker_images_snapshot
from factorlab.api.hub import HubOverview, HubOverviewService, HubRepository
from factorlab.api.india import (
    IndiaCandlesPage,
    IndiaCandlesRepository,
    IndiaDailyStatsPage,
    IndiaInstrumentsPage,
    IndiaMarketStats,
)
from factorlab.api.india_hub import (
    IndiaHubInstrument,
    IndiaHubInstrumentDays,
    IndiaHubInstrumentPage,
    IndiaHubRepository,
    default_india_history_range,
)
from factorlab.api.india_observability import (
    IndiaAnomaliesPage,
    IndiaCollectionActivity,
    IndiaCoveragePage,
    IndiaDashboard,
    IndiaFreshnessPage,
    IndiaGapsPage,
    IndiaIngestionRun,
    IndiaIngestionRunsPage,
    IndiaInstrumentSummary,
    IndiaMetricsSeries,
    IndiaObservabilityRepository,
    IndiaSourceStatusPage,
)
from factorlab.api.political import PoliticalTradesPage, PoliticalTradesRepository
from factorlab.api.political_observability import (
    PoliticalAnomaliesPage,
    PoliticalCollectionActivity,
    PoliticalCoveragePage,
    PoliticalDashboard,
    PoliticalFreshnessPage,
    PoliticalLegislatorsPage,
    PoliticalLegislatorSummary,
    PoliticalMetricsSeries,
    PoliticalObservabilityRepository,
    PoliticalSourceStatusPage,
    PoliticalTickersPage,
    PoliticalTickerSummary,
)
from factorlab.api.schema_map import (
    InvalidLayoutError,
    LayoutConflictError,
    SchemaLayoutUpdate,
    SchemaMapRepository,
    SchemaMapResponse,
    SchemaMapService,
    SharedSchemaLayout,
)
from factorlab.api.us import router as us_router

app = FastAPI(
    title="FactorLab API",
    version="0.1.0",
    description="Private read API for FactorLab research data.",
)

app.include_router(us_router, prefix="/api/v1/us", tags=["us"], dependencies=[Depends(require_api_key)])
app.include_router(us_router, prefix="/hub/api/v1/us", tags=["hub-us"])


@app.get("/hub/api/v1/docker-images", response_model=DockerImagesResponse, tags=["hub"])
def get_hub_docker_images() -> DockerImagesResponse:
    """Return the most recent host image inventory, including stale snapshots."""
    try:
        return read_docker_images_snapshot()
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Docker image snapshot is unavailable") from exc


def get_political_trades_repository() -> PoliticalTradesRepository:
    """Create a request-local ClickHouse repository."""

    return PoliticalTradesRepository.from_environment()


def get_india_candles_repository() -> IndiaCandlesRepository:
    """Create a request-local India candle repository."""

    return IndiaCandlesRepository.from_environment()


def get_india_observability_repository() -> IndiaObservabilityRepository:
    """Create a request-local India observability repository."""

    return IndiaObservabilityRepository.from_environment()


def get_india_hub_repository() -> IndiaHubRepository:
    """Create a request-local India Markets explorer repository."""

    return IndiaHubRepository.from_environment()


def get_political_observability_repository() -> PoliticalObservabilityRepository:
    """Create a request-local political observability repository."""

    return PoliticalObservabilityRepository.from_environment()


@lru_cache
def get_hub_overview_service() -> HubOverviewService:
    """Create the private web hub's cached ClickHouse reader."""

    return HubOverviewService(HubRepository.from_environment())


@lru_cache
def get_schema_map_service() -> SchemaMapService:
    """Create the live schema reader and canonical layout store."""

    return SchemaMapService(SchemaMapRepository.from_environment())


@lru_cache
def get_v2_schema_map_service() -> SchemaMapService:
    """Create the multi-database v2 schema reader and its separate layout store."""

    return SchemaMapService(SchemaMapRepository.v2_from_environment())


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/hub/api/v1/overview",
    response_model=HubOverview,
    tags=["hub"],
)
def get_hub_overview(
    service: Annotated[HubOverviewService, Depends(get_hub_overview_service)],
) -> HubOverview:
    """Return the private web hub's table inventory and schedule-aware health."""

    try:
        return service.get_overview()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="FactorLab data store is unavailable") from exc


@app.get(
    "/hub/api/v1/schema-map",
    response_model=SchemaMapResponse,
    tags=["hub"],
)
def get_hub_schema_map(
    service: Annotated[SchemaMapService, Depends(get_schema_map_service)],
) -> SchemaMapResponse:
    """Return live ClickHouse metadata, logical links, and the shared layout."""

    try:
        return service.get_schema_map()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Schema metadata is unavailable") from exc


@app.put(
    "/hub/api/v1/schema-map/layout",
    response_model=SharedSchemaLayout,
    tags=["hub"],
)
def save_hub_schema_layout(
    update: SchemaLayoutUpdate,
    service: Annotated[SchemaMapService, Depends(get_schema_map_service)],
) -> SharedSchemaLayout:
    """Replace the shared layout when the caller still has the current revision."""

    try:
        return service.save_layout(update)
    except LayoutConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidLayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The shared layout could not be saved") from exc


@app.get(
    "/hub/api/v1/schema-map/v2",
    response_model=SchemaMapResponse,
    tags=["hub"],
)
def get_hub_v2_schema_map(
    service: Annotated[SchemaMapService, Depends(get_v2_schema_map_service)],
) -> SchemaMapResponse:
    """Return live metadata for every FactorLab v2 ClickHouse namespace."""

    try:
        return service.get_schema_map()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="V2 schema metadata is unavailable") from exc


@app.put(
    "/hub/api/v1/schema-map/v2/layout",
    response_model=SharedSchemaLayout,
    tags=["hub"],
)
def save_hub_v2_schema_layout(
    update: SchemaLayoutUpdate,
    service: Annotated[SchemaMapService, Depends(get_v2_schema_map_service)],
) -> SharedSchemaLayout:
    """Save the independent shared layout for the multi-database v2 map."""

    try:
        return service.save_layout(update)
    except LayoutConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidLayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The v2 layout could not be saved") from exc


@app.get(
    "/hub/api/v1/india/dashboard",
    response_model=IndiaDashboard,
    tags=["hub-india"],
)
def get_hub_india_dashboard(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    trading_date: date | None = None,
) -> IndiaDashboard:
    """Return India collection health for the private web hub."""

    return repository.get_dashboard(trading_date=trading_date or _india_today())


@app.get(
    "/hub/api/v1/india/instruments",
    response_model=IndiaHubInstrumentPage,
    tags=["hub-india"],
)
def list_hub_india_instruments(
    repository: Annotated[IndiaHubRepository, Depends(get_india_hub_repository)],
    trading_date: date | None = None,
    scope: Literal["all", "collecting", "historical", "not_configured"] = "all",
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaHubInstrumentPage:
    """Return the India reference universe with collection and selected-day checks."""

    return repository.list_instruments(
        trading_date=trading_date or _india_today(),
        scope=scope,
        search=search,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/hub/api/v1/india/instruments/{listing_id}",
    response_model=IndiaHubInstrument,
    tags=["hub-india"],
)
def get_hub_india_instrument(
    listing_id: UUID,
    repository: Annotated[IndiaHubRepository, Depends(get_india_hub_repository)],
    trading_date: date | None = None,
) -> IndiaHubInstrument:
    """Return one instrument with its stored coverage and selected-day checks."""

    return repository.get_instrument(
        listing_id,
        trading_date=trading_date or _india_today(),
    )


@app.get(
    "/hub/api/v1/india/instruments/{listing_id}/candles",
    response_model=IndiaCandlesPage,
    tags=["hub-india"],
)
def list_hub_india_instrument_candles(
    listing_id: UUID,
    repository: Annotated[IndiaCandlesRepository, Depends(get_india_candles_repository)],
    trading_date: date | None = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> IndiaCandlesPage:
    """Return actual one-minute candle rows for one instrument and trading session."""

    return repository.list_candles(
        listing_id=listing_id,
        trading_date=trading_date or _india_today(),
        cursor=cursor,
        limit=limit,
    )


@app.get(
    "/hub/api/v1/india/instruments/{listing_id}/days",
    response_model=IndiaHubInstrumentDays,
    tags=["hub-india"],
)
def list_hub_india_instrument_days(
    listing_id: UUID,
    repository: Annotated[IndiaHubRepository, Depends(get_india_hub_repository)],
    date_from: date | None = None,
    date_to: date | None = None,
) -> IndiaHubInstrumentDays:
    """Return session-aware daily checks for one India instrument."""

    end = date_to or _india_today()
    start = date_from or default_india_history_range(end)[0]
    _validate_date_range(start, end)
    if (end - start).days > 120:
        raise HTTPException(status_code=422, detail="India instrument history cannot exceed 120 days")
    return repository.list_instrument_days(
        listing_id,
        date_from=start,
        date_to=end,
    )


@app.get(
    "/api/v1/india/candles/1min",
    response_model=IndiaCandlesPage,
    tags=["india"],
    dependencies=[Depends(require_api_key)],
)
def list_india_candles_1min(
    repository: Annotated[IndiaCandlesRepository, Depends(get_india_candles_repository)],
    symbol: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&._-]+$")
    ] = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> IndiaCandlesPage:
    """Return Indian one-minute OHLCV/OI candles, newest first."""

    if time_from and time_to and time_from > time_to:
        raise HTTPException(status_code=422, detail="time_from must be on or before time_to")
    return repository.list_candles(
        symbol=symbol,
        time_from=time_from,
        time_to=time_to,
        source=source,
        cursor=cursor,
        limit=limit,
    )


@app.get(
    "/api/v1/india/instruments",
    response_model=IndiaInstrumentsPage,
    tags=["india"],
    dependencies=[Depends(require_api_key)],
)
def list_india_instruments(
    repository: Annotated[IndiaCandlesRepository, Depends(get_india_candles_repository)],
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    status: Annotated[
        str | None, Query(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    ] = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> IndiaInstrumentsPage:
    """Return the latest synchronized Indian instrument reference universe."""

    return repository.list_instruments(
        search=search,
        status=status,
        source=source,
        cursor=cursor,
        limit=limit,
    )


@app.get(
    "/api/v1/india/stats",
    response_model=IndiaMarketStats,
    tags=["india"],
    dependencies=[Depends(require_api_key)],
)
def get_india_stats(
    repository: Annotated[IndiaCandlesRepository, Depends(get_india_candles_repository)],
    date_from: date | None = None,
    date_to: date | None = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
) -> IndiaMarketStats:
    """Return overall instrument and one-minute candle coverage metrics."""

    _validate_date_range(date_from, date_to)
    return repository.get_stats(date_from=date_from, date_to=date_to, source=source)


@app.get(
    "/api/v1/india/stats/daily",
    response_model=IndiaDailyStatsPage,
    tags=["india"],
    dependencies=[Depends(require_api_key)],
)
def list_india_daily_stats(
    repository: Annotated[IndiaCandlesRepository, Depends(get_india_candles_repository)],
    date_from: date | None = None,
    date_to: date | None = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 90,
) -> IndiaDailyStatsPage:
    """Return one-minute candle coverage grouped by Indian trading date."""

    _validate_date_range(date_from, date_to)
    return repository.list_daily_stats(
        date_from=date_from,
        date_to=date_to,
        source=source,
        limit=limit,
    )


def _validate_date_range(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")


def _india_today() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


@app.get(
    "/api/v1/india/dashboard",
    response_model=IndiaDashboard,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_india_dashboard(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    trading_date: date | None = None,
) -> IndiaDashboard:
    """Return top-level collection, coverage, freshness, and anomaly cards."""

    return repository.get_dashboard(trading_date=trading_date or _india_today())


@app.get(
    "/api/v1/india/coverage",
    response_model=IndiaCoveragePage,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_india_coverage(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    trading_date: date | None = None,
    symbol: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&._-]+$")
    ] = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    status_filter: Annotated[
        Literal["complete", "partial", "missing"] | None, Query(alias="status")
    ] = None,
    min_coverage: Annotated[float | None, Query(ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaCoveragePage:
    """Return expected-versus-actual coverage for each active series."""

    return repository.list_coverage(
        trading_date=trading_date or _india_today(),
        symbol=symbol,
        source=source,
        status_filter=status_filter,
        min_coverage=min_coverage,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/india/collection/activity",
    response_model=IndiaCollectionActivity,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_india_collection_activity(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    ingestion_date: date | None = None,
) -> IndiaCollectionActivity:
    """Return rows physically collected on an Indian calendar date."""

    return repository.get_collection_activity(ingestion_date=ingestion_date or _india_today())


@app.get(
    "/api/v1/india/freshness",
    response_model=IndiaFreshnessPage,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_india_freshness(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    stale_after_seconds: Annotated[int, Query(ge=30, le=86400)] = 300,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    status_filter: Annotated[
        Literal["live", "stale", "never_seen"] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaFreshnessPage:
    """Return freshness state for every expected instrument/contract series."""

    return repository.list_freshness(
        stale_after_seconds=stale_after_seconds,
        source=source,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/india/gaps",
    response_model=IndiaGapsPage,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_india_gaps(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    trading_date: date | None = None,
    symbol: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&._-]+$")
    ] = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaGapsPage:
    """Return contiguous missing one-minute ranges during the NSE session."""

    return repository.list_gaps(
        trading_date=trading_date or _india_today(),
        symbol=symbol,
        source=source,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/india/anomalies",
    response_model=IndiaAnomaliesPage,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_india_anomalies(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    trading_date: date | None = None,
    severity: Literal["warning", "critical"] | None = None,
    symbol: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&._-]+$")
    ] = None,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
    stale_after_seconds: Annotated[int, Query(ge=30, le=86400)] = 300,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaAnomaliesPage:
    """Detect missing, stale, invalid, duplicate, flat, and extreme series."""

    return repository.list_anomalies(
        trading_date=trading_date or _india_today(),
        severity=severity,
        symbol=symbol,
        source=source,
        stale_after_seconds=stale_after_seconds,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/india/metrics/timeseries",
    response_model=IndiaMetricsSeries,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_india_metrics_timeseries(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    metric: Literal[
        "data_points",
        "unique_instruments",
        "unique_series",
        "coverage_percent",
        "ingestion_lag",
        "missing_points",
        "anomaly_count",
    ],
    group_by: Literal["hour", "day"],
    date_from: date,
    date_to: date,
    source: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    ] = None,
) -> IndiaMetricsSeries:
    """Return dashboard-ready operational metric points."""

    _validate_date_range(date_from, date_to)
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=422, detail="Metrics range cannot exceed 366 days")
    return repository.get_metrics(
        metric=metric,
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
        source=source,
    )


@app.get(
    "/api/v1/india/instruments/{listing_id}/summary",
    response_model=IndiaInstrumentSummary,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_india_instrument_summary(
    listing_id: UUID,
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
) -> IndiaInstrumentSummary:
    """Return instrument metadata, contracts, history, gaps, and anomaly summary."""

    return repository.get_instrument_summary(listing_id)


@app.get(
    "/api/v1/india/ingestion/runs",
    response_model=IndiaIngestionRunsPage,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_india_ingestion_runs(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    pipeline: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    status_filter: Annotated[
        Literal["running", "success", "partial", "failed", "cancelled"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaIngestionRunsPage:
    """Return recent India ingestion jobs and their outcomes."""

    return repository.list_ingestion_runs(
        pipeline=pipeline,
        source=source,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/india/ingestion/runs/{run_id}",
    response_model=IndiaIngestionRun,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_india_ingestion_run(
    run_id: UUID,
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
) -> IndiaIngestionRun:
    """Return one ingestion run, including counts and any terminal error."""

    return repository.get_ingestion_run(run_id)


@app.get(
    "/api/v1/india/sources/status",
    response_model=IndiaSourceStatusPage,
    tags=["india-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_india_source_status(
    repository: Annotated[
        IndiaObservabilityRepository, Depends(get_india_observability_repository)
    ],
    stale_after_seconds: Annotated[int, Query(ge=30, le=86400)] = 600,
) -> IndiaSourceStatusPage:
    """Return latest run outcome and market-data freshness for each source pipeline."""

    return repository.list_source_status(stale_after_seconds=stale_after_seconds)


@app.get(
    "/hub/api/v1/political/dashboard",
    response_model=PoliticalDashboard,
    tags=["hub-political"],
)
def get_hub_political_dashboard(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    as_of_date: date | None = None,
) -> PoliticalDashboard:
    """Return political disclosure totals and quality metrics for the private hub."""

    return repository.get_dashboard(as_of_date=as_of_date or datetime.now(UTC).date())


@app.get(
    "/hub/api/v1/political/coverage",
    response_model=PoliticalCoveragePage,
    tags=["hub-political"],
)
def list_hub_political_coverage(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> PoliticalCoveragePage:
    """Return political filing and entity-resolution coverage for the private hub."""

    return repository.list_coverage(year=year)


@app.get(
    "/hub/api/v1/political/metrics/timeseries",
    response_model=PoliticalMetricsSeries,
    tags=["hub-political"],
)
def get_hub_political_metrics_timeseries(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    metric: Literal[
        "filings",
        "trades",
        "unique_legislators",
        "unique_tickers",
        "amount_min",
        "late_disclosures",
        "unmatched_legislators",
    ],
    date_basis: Literal["filing", "transaction", "ingestion"],
    group_by: Literal["day", "month"],
    date_from: date,
    date_to: date,
) -> PoliticalMetricsSeries:
    """Return chart-ready political metrics for the private hub."""

    _validate_date_range(date_from, date_to)
    if (date_to - date_from).days > 3660:
        raise HTTPException(status_code=422, detail="Metrics range cannot exceed ten years")
    if metric == "filings" and date_basis == "transaction":
        raise HTTPException(status_code=422, detail="Filings have no transaction date")
    return repository.get_metrics(
        metric=metric,
        date_basis=date_basis,
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
    )


@app.get(
    "/hub/api/v1/political/trades",
    response_model=PoliticalTradesPage,
    tags=["hub-political"],
)
def list_hub_political_trades(
    repository: Annotated[PoliticalTradesRepository, Depends(get_political_trades_repository)],
    ticker: Annotated[
        str | None, Query(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9.-]+$")
    ] = None,
    bioguide_id: Annotated[
        str | None, Query(min_length=7, max_length=7, pattern=r"^[A-Za-z0-9]+$")
    ] = None,
    chamber: Literal["house", "senate"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PoliticalTradesPage:
    """Return political trades for the private hub disclosure explorer."""

    _validate_date_range(date_from, date_to)
    return repository.list_trades(
        ticker=ticker,
        bioguide_id=bioguide_id,
        chamber=chamber,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )


@app.get(
    "/api/v1/political/dashboard",
    response_model=PoliticalDashboard,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_political_dashboard(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    as_of_date: date | None = None,
) -> PoliticalDashboard:
    """Return top-level political collection, matching, freshness, and quality cards."""

    return repository.get_dashboard(as_of_date=as_of_date or datetime.now(UTC).date())


@app.get(
    "/api/v1/political/coverage",
    response_model=PoliticalCoveragePage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_coverage(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> PoliticalCoveragePage:
    """Return filing parse and entity-resolution coverage by chamber."""

    return repository.list_coverage(year=year)


@app.get(
    "/api/v1/political/collection/activity",
    response_model=PoliticalCollectionActivity,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_political_collection_activity(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    ingestion_date: date | None = None,
) -> PoliticalCollectionActivity:
    """Return political filing and trade rows physically collected on a UTC date."""

    return repository.get_collection_activity(
        ingestion_date=ingestion_date or datetime.now(UTC).date()
    )


@app.get(
    "/api/v1/political/freshness",
    response_model=PoliticalFreshnessPage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_freshness(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    stale_after_seconds: Annotated[int, Query(ge=300, le=31_536_000)] = 172800,
) -> PoliticalFreshnessPage:
    """Return freshness for political reference, filing, and trade datasets."""

    return repository.list_freshness(stale_after_seconds=stale_after_seconds)


@app.get(
    "/api/v1/political/anomalies",
    response_model=PoliticalAnomaliesPage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_anomalies(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    date_from: date | None = None,
    date_to: date | None = None,
    severity: Literal["warning", "critical"] | None = None,
    anomaly_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> PoliticalAnomaliesPage:
    """Return unparsed, late, unmatched, and internally invalid disclosures."""

    _validate_date_range(date_from, date_to)
    return repository.list_anomalies(
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        anomaly_type=anomaly_type,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/political/metrics/timeseries",
    response_model=PoliticalMetricsSeries,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_political_metrics_timeseries(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    metric: Literal[
        "filings",
        "trades",
        "unique_legislators",
        "unique_tickers",
        "amount_min",
        "late_disclosures",
        "unmatched_legislators",
    ],
    date_basis: Literal["filing", "transaction", "ingestion"],
    group_by: Literal["day", "month"],
    date_from: date,
    date_to: date,
) -> PoliticalMetricsSeries:
    """Return dashboard-ready political activity and quality metrics."""

    _validate_date_range(date_from, date_to)
    if (date_to - date_from).days > 3660:
        raise HTTPException(status_code=422, detail="Metrics range cannot exceed ten years")
    if metric == "filings" and date_basis == "transaction":
        raise HTTPException(status_code=422, detail="Filings have no transaction date")
    return repository.get_metrics(
        metric=metric,
        date_basis=date_basis,
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
    )


@app.get(
    "/api/v1/political/legislators",
    response_model=PoliticalLegislatorsPage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_legislators(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    chamber: Literal["rep", "sen"] | None = None,
    state_code: Annotated[
        str | None, Query(alias="state", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    ] = None,
    party: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
    in_office: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> PoliticalLegislatorsPage:
    """Search and page through canonical congressional legislators."""

    return repository.list_legislators(
        search=search,
        chamber=chamber,
        state_code=state_code.upper() if state_code else None,
        party=party,
        in_office=in_office,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/political/legislators/{bioguide_id}/summary",
    response_model=PoliticalLegislatorSummary,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_political_legislator_summary(
    bioguide_id: Annotated[str, Path(min_length=7, max_length=7, pattern=r"^[A-Za-z0-9]+$")],
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
) -> PoliticalLegislatorSummary:
    """Return one legislator with aggregated filing and trade activity."""

    return repository.get_legislator_summary(bioguide_id.upper())


@app.get(
    "/api/v1/political/tickers",
    response_model=PoliticalTickersPage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_tickers(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    search: Annotated[str | None, Query(min_length=1, max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> PoliticalTickersPage:
    """Return unique disclosed tickers with aggregate political-trade activity."""

    return repository.list_tickers(search=search, limit=limit, offset=offset)


@app.get(
    "/api/v1/political/tickers/{ticker}/summary",
    response_model=PoliticalTickerSummary,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_political_ticker_summary(
    ticker: Annotated[str, Path(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.-]+$")],
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
) -> PoliticalTickerSummary:
    """Return ticker totals, leading legislators, and transaction-type mix."""

    return repository.get_ticker_summary(ticker.upper())


@app.get(
    "/api/v1/political/ingestion/runs",
    response_model=IndiaIngestionRunsPage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_ingestion_runs(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    pipeline: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    status_filter: Annotated[
        Literal["running", "success", "partial", "failed", "cancelled"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> IndiaIngestionRunsPage:
    """Return political ingestion run history."""

    return repository.list_ingestion_runs(
        pipeline=pipeline,
        source=source,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/political/ingestion/runs/{run_id}",
    response_model=IndiaIngestionRun,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def get_political_ingestion_run(
    run_id: UUID,
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
) -> IndiaIngestionRun:
    """Return one political ingestion run."""

    return repository.get_ingestion_run(run_id)


@app.get(
    "/api/v1/political/sources/status",
    response_model=PoliticalSourceStatusPage,
    tags=["political-observability"],
    dependencies=[Depends(require_api_key)],
)
def list_political_source_status(
    repository: Annotated[
        PoliticalObservabilityRepository, Depends(get_political_observability_repository)
    ],
    stale_after_seconds: Annotated[int, Query(ge=300, le=31_536_000)] = 172800,
) -> PoliticalSourceStatusPage:
    """Return political pipeline outcome and dataset freshness."""

    return repository.list_source_status(stale_after_seconds=stale_after_seconds)


@app.get(
    "/api/v1/political/trades",
    response_model=PoliticalTradesPage,
    tags=["political"],
    dependencies=[Depends(require_api_key)],
)
def list_political_trades(
    repository: Annotated[PoliticalTradesRepository, Depends(get_political_trades_repository)],
    ticker: Annotated[
        str | None, Query(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9.-]+$")
    ] = None,
    bioguide_id: Annotated[
        str | None, Query(min_length=7, max_length=7, pattern=r"^[A-Za-z0-9]+$")
    ] = None,
    chamber: Literal["house", "senate"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PoliticalTradesPage:
    """Return latest-version political trades in reverse chronological order."""

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )
    return repository.list_trades(
        ticker=ticker,
        bioguide_id=bioguide_id,
        chamber=chamber,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )


_default_web_dist = (
    FileSystemPath(__file__).resolve().parents[3] / "src" / "factorlab" / "webui" / "dist"
)
_web_dist = FileSystemPath(os.getenv("FACTORLAB_WEB_DIST", str(_default_web_dist)))
if (_web_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="hub-assets")


def _hub_index() -> FileResponse:
    index = _web_dist / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="FactorLab Hub frontend is not built")
    return FileResponse(index)


@app.get("/", include_in_schema=False)
def hub_home() -> FileResponse:
    return _hub_index()


@app.get("/roadmap", include_in_schema=False)
def hub_roadmap() -> FileResponse:
    return _hub_index()


@app.get("/docker-images", include_in_schema=False)
def hub_docker_images() -> FileResponse:
    return _hub_index()


@app.get("/india", include_in_schema=False)
def hub_india_markets() -> FileResponse:
    return _hub_index()


@app.get("/india/instruments/{listing_id}", include_in_schema=False)
def hub_india_instrument(listing_id: UUID) -> FileResponse:
    return _hub_index()


@app.get("/us", include_in_schema=False)
def hub_us_markets() -> FileResponse:
    return _hub_index()


@app.get("/political", include_in_schema=False)
def hub_political_data() -> FileResponse:
    return _hub_index()


@app.get("/schema", include_in_schema=False)
def hub_schema_map() -> FileResponse:
    return _hub_index()


@app.get("/schema/v2", include_in_schema=False)
def hub_v2_schema_map() -> FileResponse:
    return _hub_index()
