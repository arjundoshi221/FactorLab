"""Monitoring reads ClickHouse independently of the UDP daemon."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from factorlab.clickhouse import ClickHouse, rows
from factorlab.core.paths import home

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
