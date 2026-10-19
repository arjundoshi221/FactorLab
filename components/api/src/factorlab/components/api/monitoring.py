"""Monitoring reads ClickHouse independently of the UDP daemon."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from factorlab.clickhouse import ClickHouse, rows
from factorlab.core.paths import home

from .monitoring_errors import error_history

router = APIRouter()


def repository():
    try:
        db = ClickHouse.from_environment()
    except Exception as exc:
        raise HTTPException(503, "Service monitoring is unavailable") from exc
    try:
        yield db.client
    finally:
        db.close()


Client = Annotated[Any, Depends(repository)]


@router.get("/errors")
def errors(
    client: Client,
    hours: Annotated[int, Query(ge=1, le=720)] = 168,
    service_id: str = "",
    severity: Literal["all", "error", "fatal", "warning"] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    return error_history(client, hours=hours, service_id=service_id, severity=severity, limit=limit)


@router.get("/daily")
def daily(client: Client):
    """Aggregate stored telemetry, never infer uptime from sampled events."""
    now = datetime.now(UTC)
    try:
        items = rows(
            client.query(
                """SELECT service_id, toDate(received_at, 'UTC') AS day,
                count() AS reports, countIf(type = 'err') AS errors,
                countIf(type = 'warn') AS warnings, countIf(type = 'restart') AS restarts,
                countIf(type = 'state' AND state = 'down') AS outages
            FROM meta.service_events
            WHERE received_at >= {since:DateTime64(3, 'UTC')}
              AND received_at <= {now:DateTime64(3, 'UTC')}
            GROUP BY service_id, day ORDER BY service_id, day""",
                parameters={
                    "since": now.replace(hour=0, minute=0, second=0, microsecond=0)
                    - timedelta(days=6),
                    "now": now,
                },
                settings={"readonly": 2, "max_execution_time": 5},
            )
        )
        return {"generated_at": now, "days": items}
    except Exception as exc:
        raise HTTPException(503, "Daily monitoring is unavailable") from exc


@router.get("/coverage")
def coverage(client: Client):
    """Latest recorded session per feed within a bounded 30-day window."""
    now = datetime.now(UTC)
    try:
        return {"generated_at": now, "feeds": coverage_feeds(client, now)}
    except Exception as exc:
        raise HTTPException(503, "Data coverage is unavailable") from exc


def coverage_feeds(client: Any, now: datetime) -> list[dict[str, Any]]:
    """Latest checked session per feed, plus active series that have no check."""
    items = rows(
        client.query(
            """SELECT country_code, source, resolution, trade_date,
            count() AS checked_series, sum(c.expected) AS expected,
            sum(c.actual) AS actual, sum(c.missing) AS missing,
            countIf(c.missing > 0) AS affected_series, max(c.ingested_at) AS checked_at
        FROM meta.session_coverage AS c FINAL
        WHERE trade_date >= {since:Date} AND trade_date <= {today:Date}
        GROUP BY country_code, source, resolution, trade_date
        ORDER BY country_code, source, resolution, trade_date DESC
        LIMIT 1 BY country_code, source, resolution""",
            parameters={"since": (now - timedelta(days=30)).date(), "today": now.date()},
            settings={"readonly": 2, "max_execution_time": 5},
        )
    )
    expected = rows(
        client.query(
            """SELECT e.country_code, e.source, e.resolution,
                uniqExact(e.listing_id) AS active_series,
                uniqExactIf(e.listing_id, isNull(c.listing_id)) AS unreported_series
            FROM (SELECT DISTINCT country_code, source, resolution, listing_id
                FROM meta.expected_series FINAL WHERE active AND isNull(contract_id)) AS e
            LEFT JOIN (
                SELECT country_code, source, resolution, listing_id
                FROM meta.session_coverage FINAL
                WHERE trade_date >= {since:Date} AND trade_date <= {today:Date}
                  AND (country_code, source, resolution, trade_date) IN (
                    SELECT country_code, source, resolution, max(trade_date)
                    FROM meta.session_coverage FINAL
                    WHERE trade_date >= {since:Date} AND trade_date <= {today:Date}
                    GROUP BY country_code, source, resolution)
            ) AS c ON e.country_code = c.country_code AND e.source = c.source
                AND e.resolution = c.resolution AND e.listing_id = c.listing_id
            GROUP BY e.country_code, e.source, e.resolution""",
            parameters={"since": (now - timedelta(days=30)).date(), "today": now.date()},
            settings={"readonly": 2, "max_execution_time": 5, "join_use_nulls": 1},
        )
    )
    by_feed = {(item["country_code"], item["source"], item["resolution"]): item for item in items}
    for feed in expected:
        key = (feed["country_code"], feed["source"], feed["resolution"])
        if key not in by_feed:
            item = {
                "country_code": feed["country_code"],
                "source": feed["source"],
                "resolution": feed["resolution"],
                "trade_date": None,
                "checked_series": 0,
                "expected": 0,
                "actual": 0,
                "missing": 0,
                "affected_series": 0,
                "checked_at": None,
            }
            items.append(item)
            by_feed[key] = item
        by_feed[key].update(
            active_series=feed["active_series"], unreported_series=feed["unreported_series"]
        )
    return items


def registry():
    path = Path("/app/monitor-services.yaml")
    if not path.exists():
        path = home() / "deploy" / "monitor-services.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def services_response(items, expected, now):
    by_id = {row["service_id"]: row for row in items}
    result = []
    for id in sorted(set(expected) | set(by_id)):
        row = dict(by_id.get(id, {"service_id": id, "status": "unknown"}))
        row["registered"] = id in expected
        row["monitor"] = expected.get(id)
        seen = row.get("last_seen_at")
        started = row.get("instance_started_at")
        row["last_seen_age_s"] = max(0, (now - seen).total_seconds()) if seen else None
        row["uptime_s"] = max(0, (seen - started).total_seconds()) if seen and started else None
        result.append(row)
    watcher = by_id.get("monitor")
    offline = (
        not watcher
        or watcher["status"] != "up"
        or (now - watcher["updated_at"]).total_seconds() > 15
    )
    return {"services": result, "monitor_offline": offline, "generated_at": now}


@router.get("/services")
def services(client: Client):
    try:
        items = rows(
            client.query(
                "SELECT * FROM meta.service_status FINAL",
                settings={"readonly": 2, "max_execution_time": 5},
            )
        )
        return services_response(items, registry(), datetime.now(UTC))
    except Exception as exc:
        raise HTTPException(503, "Service monitoring is unavailable") from exc


@router.get("/events")
def events(
    client: Client,
    service_id: str = "",
    type: Literal["hb", "warn", "err", "state", "restart"] | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    if since is not None and since.tzinfo is None:
        raise HTTPException(422, "since must include a timezone")
    predicates = []
    parameters: dict[str, Any] = {"limit": limit}
    if service_id:
        predicates.append("service_id = {service_id:String}")
        parameters["service_id"] = service_id
    if type:
        predicates.append("type = {type:String}")
        parameters["type"] = type
    if since:
        predicates.append("received_at >= {since:DateTime64(3, 'UTC')}")
        parameters["since"] = since
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    try:
        items = rows(
            client.query(
                "SELECT * FROM meta.service_events"
                + where
                + " ORDER BY received_at DESC LIMIT {limit:UInt32}",
                parameters=parameters,
                settings={"readonly": 2, "max_execution_time": 5},
            )
        )
        return {"events": items}
    except Exception as exc:
        raise HTTPException(503, "Monitoring events are unavailable") from exc
