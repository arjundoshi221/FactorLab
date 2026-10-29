"""What each ingestion service collects: universe, resolution, cadence and reported config.

Declared values come from the pipeline catalogue; observed ones from the latest
``meta.ingestion_runs`` row per pipeline (its ``universe_id`` and the ``config`` the
collector records in ``metadata_json``), the run gaps of the last 24 hours, and the
active ``meta.expected_series`` membership. Status and last run reuse the Pipelines
page's schedule-aware cards. A failed observation is listed in ``unavailable`` and
leaves its fields empty; it never reads as a clean plan.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from factorlab.clickhouse import rows

from .catalog_pipelines import INDEX_PIPELINE, PIPELINES, PipelineInfo

SETTINGS = {"readonly": 2, "max_execution_time": 5, "max_result_rows": 10_000}
CACHE_SECONDS = 60.0
RUN_LOOKBACK = timedelta(days=7)
GAP_LOOKBACK = timedelta(days=1)

# Deployable component of each service (components/*/component.yaml; tested).
SERVICE_COMPONENTS = {
    "universe-us": "ingest-us",
    "ingest-us": "ingest-us",
    "indexes-us": "ingest-us",
    "futures-us": "ingest-us",
    "ingest-india": "ingest-india",
    "ingest-political": "ingest-political",
    "ibkr-snapshot": "ingest-broker",
}

Flag = Literal["universe_mismatch", "several_universes", "config_unreported"]


class Membership(BaseModel):
    resolution: str
    source: str
    active_series: int
    contracts: int


class CollectionPipeline(BaseModel):
    id: str
    label: str
    schedule: str
    scheduled: bool
    per_symbol_runs: bool
    declared_universe: str
    resolution: str | None
    expected_interval_seconds: int | None
    status: str
    status_reason: str
    last_status: str | None
    last_started_at: datetime | None
    runs_24h: int
    universe: str | None
    universes_7d: list[str]
    requested_series: int | None
    config: dict[str, Any] | None
    median_gap_seconds: float | None
    members: list[Membership]
    flags: list[Flag]


class CollectionService(BaseModel):
    service: str
    pipelines: list[CollectionPipeline]


class CollectionComponent(BaseModel):
    component: str
    services: list[CollectionService]


class CollectionPlan(BaseModel):
    generated_at: datetime
    components: list[CollectionComponent]
    unavailable: list[str]


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def latest_runs(client: Any, now: datetime) -> dict[str, dict[str, Any]]:
    """Universe, requested series and metadata of each pipeline's newest run in a week."""
    ids = [p.id for p in PIPELINES if p.id != INDEX_PIPELINE]
    items = rows(
        client.query(
            """SELECT pipeline,
                argMax(universe_id, (started_at, version)) AS universe_id,
                argMax(requested_series, (started_at, version)) AS requested_series,
                argMax(metadata_json, (started_at, version)) AS metadata_json,
                arraySort(groupUniqArray(10)(universe_id)) AS universes_7d
            FROM meta.ingestion_runs
            WHERE has({pipelines:Array(String)}, pipeline) AND started_at >= parseDateTime64BestEffort({since:String}, 3, 'UTC')
            GROUP BY pipeline""",
            parameters={"pipelines": ids, "since": stamp(now - RUN_LOOKBACK)},
            settings=SETTINGS,
        )
    )
    return {str(item["pipeline"]): item for item in items}


def median_gaps(client: Any, now: datetime) -> dict[str, float]:
    """Median seconds between consecutive run starts over 24 hours (not per-symbol runs)."""
    ids = [p.id for p in PIPELINES if p.id != INDEX_PIPELINE and not p.per_symbol_runs]
    items = rows(
        client.query(
            """SELECT pipeline, arrayReduce('median', arrayPopFront(arrayDifference(
                    arraySort(groupUniqArray(toUnixTimestamp(started_at)))))) AS median_gap
            FROM meta.ingestion_runs
            WHERE has({pipelines:Array(String)}, pipeline) AND started_at >= parseDateTime64BestEffort({since:String}, 3, 'UTC')
            GROUP BY pipeline""",
            parameters={"pipelines": ids, "since": stamp(now - GAP_LOOKBACK)},
            settings=SETTINGS,
        )
    )
    gaps = {}
    for item in items:
        value = item.get("median_gap")
        if value is not None and not math.isnan(float(value)):
            gaps[str(item["pipeline"])] = float(value)
    return gaps


def memberships(client: Any) -> dict[str, list[Membership]]:
    """Active expected series per universe, split by source and resolution."""
    items = rows(
        client.query(
            """SELECT universe, source, resolution, count() AS active_series,
                countIf(isNotNull(contract_id)) AS contracts
            FROM meta.expected_series FINAL
            WHERE active
            GROUP BY universe, source, resolution
            ORDER BY universe, source, resolution""",
            settings=SETTINGS,
        )
    )
    by_universe: dict[str, list[Membership]] = {}
    for item in items:
        by_universe.setdefault(str(item["universe"]), []).append(
            Membership(
                resolution=str(item["resolution"]),
                source=str(item["source"]),
                active_series=int(item["active_series"]),
                contracts=int(item["contracts"]),
            )
        )
    return by_universe


def reported_config(metadata_json: Any) -> dict[str, Any] | None:
    try:
        metadata = json.loads(metadata_json or "{}")
    except (TypeError, ValueError):
        return None
    config = metadata.get("config") if isinstance(metadata, dict) else None
    return config if isinstance(config, dict) else None


def flags_for(pipeline: PipelineInfo, run: dict[str, Any] | None) -> list[Flag]:
    if run is None:
        return []
    flags: list[Flag] = []
    universe = str(run.get("universe_id") or "")
    if pipeline.universe and universe and universe != pipeline.universe:
        flags.append("universe_mismatch")
    if len([u for u in run.get("universes_7d") or [] if u]) > 1:
        flags.append("several_universes")
    if reported_config(run.get("metadata_json")) is None:
        flags.append("config_unreported")
    return flags


def plan_row(
    pipeline: PipelineInfo,
    card: Any,
    run: dict[str, Any] | None,
    gap: float | None,
    members: dict[str, list[Membership]],
) -> CollectionPipeline:
    universe = str(run["universe_id"]) if run and run.get("universe_id") else None
    return CollectionPipeline(
        id=pipeline.id,
        label=pipeline.label,
        schedule=pipeline.schedule,
        scheduled=pipeline.scheduled,
        per_symbol_runs=pipeline.per_symbol_runs,
        declared_universe=pipeline.universe,
        resolution=pipeline.resolution,
        expected_interval_seconds=pipeline.expected_interval_seconds,
        status=card.status if card else "unknown",
        status_reason=card.status_reason if card else "Pipeline health is unavailable.",
        last_status=card.last_status if card else None,
        last_started_at=card.last_started_at if card else None,
        runs_24h=card.runs_24h if card else 0,
        universe=universe,
        universes_7d=[str(u) for u in (run or {}).get("universes_7d") or [] if u],
        requested_series=int(run["requested_series"]) if run else None,
        config=reported_config(run.get("metadata_json")) if run else None,
        median_gap_seconds=gap,
        members=members.get(universe or pipeline.universe, []),
        flags=flags_for(pipeline, run),
    )


def build_collection_plan(client: Any, catalog: Any, now: datetime | None = None) -> CollectionPlan:
    """Group every ingestion pipeline by component and service with what it collects."""
    now = now or datetime.now(UTC)
    cards = {card.id: card for card in catalog.pipelines().pipelines}
    unavailable: list[str] = []

    def observe(name: str, read: Callable[[], Any], empty: Any) -> Any:
        try:
            return read()
        except Exception:  # noqa: BLE001 - one failed read must not hide the others
            unavailable.append(name)
            return empty

    runs = observe("runs", lambda: latest_runs(client, now), {})
    gaps = observe("cadence", lambda: median_gaps(client, now), {})
    members = observe("membership", lambda: memberships(client), {})
    grouped: dict[str, dict[str, list[CollectionPipeline]]] = {}
    for pipeline in PIPELINES:
        component = SERVICE_COMPONENTS.get(pipeline.service, pipeline.service)
        grouped.setdefault(component, {}).setdefault(pipeline.service, []).append(
            plan_row(
                pipeline,
                cards.get(pipeline.id),
                runs.get(pipeline.id),
                gaps.get(pipeline.id),
                members,
            )
        )
    return CollectionPlan(
        generated_at=now,
        components=[
            CollectionComponent(
                component=component,
                services=[
                    CollectionService(service=service, pipelines=items)
                    for service, items in services.items()
                ],
            )
            for component, services in grouped.items()
        ],
        unavailable=unavailable,
    )


class PlanCache:
    def __init__(self, seconds: float = CACHE_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._value: tuple[float, CollectionPlan] | None = None

    def get(self, build: Callable[[], CollectionPlan]) -> CollectionPlan:
        with self._lock:
            if self._value and self._value[0] > time.monotonic():
                return self._value[1]
        value = build()
        with self._lock:
            self._value = (time.monotonic() + self.seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._value = None


CACHE = PlanCache()
