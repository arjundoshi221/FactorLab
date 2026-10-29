"""Collection plan: declared universe and cadence beside what each service reports."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from factorlab.components.api.app import app, get_catalog_service
from factorlab.components.api.catalog_pipelines import PIPELINES
from factorlab.components.api.collection_plan import (
    CACHE,
    SERVICE_COMPONENTS,
    build_collection_plan,
)
from factorlab.components.api.monitoring import repository

NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[3]


def result(rows_):
    columns = list(rows_[0]) if rows_ else []
    return SimpleNamespace(
        column_names=columns, result_rows=[tuple(row[c] for c in columns) for row in rows_]
    )


def fake_client(*, runs=(), gaps=(), members=(), fail=()):
    queries = []

    def query(sql, **kwargs):
        queries.append((sql, kwargs))
        for name, data in (("argMax", runs), ("median", gaps), ("expected_series", members)):
            if name in sql:
                if name in fail:
                    raise RuntimeError("boom")
                return result(list(data))
        raise AssertionError(sql)

    return SimpleNamespace(query=query), queries


def card(id, **extra):
    return SimpleNamespace(
        id=id,
        status=extra.get("status", "healthy"),
        status_reason=extra.get("status_reason", ""),
        last_status=extra.get("last_status", "success"),
        last_started_at=extra.get("last_started_at", NOW),
        runs_24h=extra.get("runs_24h", 10),
    )


def catalog(*cards):
    return SimpleNamespace(pipelines=lambda: SimpleNamespace(pipelines=list(cards)))


def run(pipeline, universe, config=None, universes=None):
    metadata = {"symbol": "AAPL", **({"config": config} if config is not None else {})}
    return {
        "pipeline": pipeline,
        "universe_id": universe,
        "requested_series": 3,
        "metadata_json": json.dumps(metadata),
        "universes_7d": universes or [universe],
    }


def row(plan, pipeline_id):
    for component in plan.components:
        for service in component.services:
            for item in service.pipelines:
                if item.id == pipeline_id:
                    return component.component, service.service, item
    raise KeyError(pipeline_id)


def test_groups_every_pipeline_by_component_and_service():
    client, _ = fake_client()
    plan = build_collection_plan(client, catalog(), NOW)
    assert row(plan, "us_live")[:2] == ("ingest-us", "ingest-us")
    assert row(plan, "us_futures_quotes")[:2] == ("ingest-us", "futures-us")
    assert row(plan, "ibkr_broker_snapshot")[:2] == ("ingest-broker", "ibkr-snapshot")
    assert row(plan, "india_intraday_1min")[:2] == ("ingest-india", "ingest-india")
    listed = {item.id for c in plan.components for s in c.services for item in s.pipelines}
    assert listed == {p.id for p in PIPELINES}
    # No health card: unknown, never healthy.
    assert row(plan, "us_live")[2].status == "unknown"


def test_reports_observed_universe_config_cadence_and_membership():
    config = {"minute_universe": "us_liquid_250", "minute_tier_size": 250, "live_interval_s": 300}
    client, queries = fake_client(
        runs=[
            run("us_live", "us_liquid_250", config),
            run("us_recovery_daily", "us_configured", config),
            run("us_futures_quotes", "us_futures", {"interval_s": 60}),
        ],
        gaps=[{"pipeline": "us_futures_quotes", "median_gap": 60.0}],
        members=[
            {
                "universe": "us_liquid_250",
                "source": "schwab",
                "resolution": "1min",
                "active_series": 250,
                "contracts": 0,
            },
            {
                "universe": "us_configured",
                "source": "schwab",
                "resolution": "daily",
                "active_series": 2611,
                "contracts": 0,
            },
        ],
    )
    plan = build_collection_plan(client, catalog(card("us_live", runs_24h=19_500)), NOW)
    live = row(plan, "us_live")[2]
    assert (live.universe, live.declared_universe, live.flags) == (
        "us_liquid_250",
        "us_liquid_250",
        [],
    )
    assert live.config == config
    assert [(m.resolution, m.active_series) for m in live.members] == [("1min", 250)]
    assert (live.status, live.runs_24h) == ("healthy", 19_500)
    daily = row(plan, "us_recovery_daily")[2]
    assert [(m.resolution, m.active_series) for m in daily.members] == [("daily", 2611)]
    assert row(plan, "us_futures_quotes")[2].median_gap_seconds == 60.0
    assert plan.unavailable == []
    for _, kwargs in queries:
        assert kwargs["settings"]["readonly"] == 2
        assert kwargs["settings"]["max_execution_time"] == 5


def test_flags_mismatched_mixed_and_unreported_runs():
    client, _ = fake_client(
        runs=[
            run("us_recovery_1min", "pilot"),
            run("india_intraday_1min", "full_nse_eq", {"mode": "full"}, ["demo", "full_nse_eq"]),
        ]
    )
    plan = build_collection_plan(client, catalog(), NOW)
    assert row(plan, "us_recovery_1min")[2].flags == ["universe_mismatch", "config_unreported"]
    assert row(plan, "india_intraday_1min")[2].flags == ["several_universes"]
    # A pipeline with no run in the window raises no flags of its own.
    assert row(plan, "political_bootstrap")[2].flags == []


def test_a_failed_observation_is_listed_not_hidden():
    client, _ = fake_client(runs=[run("us_live", "us_liquid_250", {})], fail={"median"})
    plan = build_collection_plan(client, catalog(), NOW)
    assert plan.unavailable == ["cadence"]
    assert row(plan, "us_live")[2].universe == "us_liquid_250"


def test_route_is_cached_and_maps_catalog_failure_to_503():
    client, _ = fake_client()
    healthy = catalog()
    CACHE.clear()
    app.dependency_overrides[repository] = lambda: client
    app.dependency_overrides[get_catalog_service] = lambda: healthy
    try:
        response = TestClient(app).get("/hub/api/v1/monitoring/collection-plan")
        assert response.status_code == 200
        assert {c["component"] for c in response.json()["components"]} >= {"ingest-us"}

        def broken():
            raise RuntimeError("down")

        unavailable = SimpleNamespace(pipelines=broken)
        CACHE.clear()
        app.dependency_overrides[get_catalog_service] = lambda: unavailable
        assert TestClient(app).get("/hub/api/v1/monitoring/collection-plan").status_code == 503
    finally:
        CACHE.clear()
        app.dependency_overrides.clear()


def test_service_components_match_the_manifests():
    declared = {}
    for manifest in (REPO / "components").glob("*/component.yaml"):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        for service in data.get("services", []):
            declared[service["name"]] = data["name"]
    assert {s: declared[s] for s in SERVICE_COMPONENTS} == SERVICE_COMPONENTS
    ingest_services = {s for s, c in declared.items() if c.startswith("ingest-")}
    assert ingest_services == {p.service for p in PIPELINES}
    assert ingest_services == set(SERVICE_COMPONENTS)
