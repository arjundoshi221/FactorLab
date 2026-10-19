"""Error history is independent of heartbeat liveness and sampled event activity."""

from datetime import UTC, datetime, timedelta
from typing import Any

from factorlab.clickhouse import rows

from .catalog_query import scrub_text
from .source_health import source_state

PIPELINE_SERVICES = {
    "ibkr_broker_snapshot": "ibkr-snapshot",
    "ibkr_broker_simulation": "ibkr-snapshot",
    "us_universe_sync": "universe-us",
    "us_live": "ingest-us",
    "us_recovery_daily": "ingest-us",
    "us_recovery_1min": "ingest-us",
    "india_intraday_1min": "ingest-india",
    "india_historical_1min_recovery": "ingest-india",
    "india_reference_premarket": "ingest-india",
    "political_bootstrap": "ingest-political",
}


def error_history(client: Any, *, hours: int, service_id: str, severity: str, limit: int):
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    parameters: dict[str, Any] = {"since": since, "now": now, "limit": limit + 1}
    settings = {
        "readonly": 2,
        "max_execution_time": 5,
        "max_result_rows": 1000,
        "max_memory_usage": 256 * 1024 * 1024,
    }
    unavailable = []

    def read(section, sql, params):
        try:
            return rows(client.query(sql, parameters=params, settings=settings))
        except Exception:  # noqa: BLE001 - one unavailable history must not hide other evidence
            unavailable.append(section)
            return []

    where = " AND service_id = {service_id:String}" if service_id else ""
    kinds = {"all": ["err", "warn"], "error": ["err"], "fatal": ["err"], "warning": ["warn"]}
    telemetry = read(
        "telemetry",
        """
        SELECT service_id, type, severity, error_type, message,
            min(received_at) AS first_seen, max(received_at) AS last_seen,
            count() AS occurrences, argMax(instance_id, received_at) AS instance_id,
            argMax(host, received_at) AS host
        FROM meta.service_events
        WHERE received_at >= {since:DateTime64(3, 'UTC')}
          AND received_at <= {now:DateTime64(3, 'UTC')}
          AND has({kinds:Array(String)}, type)
        """
        + where
        + (" AND severity = 'fatal'" if severity == "fatal" else "")
        + """
        GROUP BY service_id, type, severity, error_type, message
        ORDER BY last_seen DESC LIMIT {limit:UInt32}
        """,
        {**parameters, "service_id": service_id, "kinds": kinds[severity]},
    )
    runs = []
    successes = []
    if severity in ("all", "error"):
        pipeline_where = " AND has({pipelines:Array(String)}, pipeline)" if service_id else ""
        params = {
            **parameters,
            "pipelines": [key for key, value in PIPELINE_SERVICES.items() if value == service_id]
            + [service_id],
        }
        runs = read(
            "pipeline_runs",
            """
            SELECT pipeline, source, status, ifNull(error, '') AS message,
                min(started_at) AS first_seen, max(started_at) AS last_seen,
                count() AS occurrences, argMax(run_id, started_at) AS run_id,
                sum(failed_series) AS failed_series
            FROM meta.ingestion_runs FINAL
            WHERE started_at >= {since:DateTime64(3, 'UTC')}
              AND started_at <= {now:DateTime64(3, 'UTC')}
              AND status IN ('failed', 'partial')
            """
            + pipeline_where
            + """
            GROUP BY pipeline, source, status, message
            ORDER BY last_seen DESC LIMIT {limit:UInt32}
            """,
            params,
        )
        successes = read(
            "recovery_evidence",
            """
            SELECT pipeline, source, max(started_at) AS last_success
            FROM meta.ingestion_runs FINAL
            WHERE started_at >= {since:DateTime64(3, 'UTC')}
              AND started_at <= {now:DateTime64(3, 'UTC')} AND status = 'success'
            """
            + pipeline_where
            + " GROUP BY pipeline, source LIMIT 500",
            params,
        )
    sources = read(
        "source_status",
        """
        SELECT country_code, source, status, detail, checked_at
        FROM meta.source_status FINAL ORDER BY country_code, source LIMIT 500
        """,
        {},
    )
    last_success = {(item["pipeline"], item["source"]): item["last_success"] for item in successes}
    issues = []
    for item in telemetry:
        issues.append(
            {
                **item,
                "origin": "telemetry",
                "severity": "warning" if item["type"] == "warn" else item["severity"] or "error",
                "pipeline": None,
                "last_success": None,
                "recovery": "unconfirmed",
            }
        )
    for item in runs:
        success = last_success.get((item["pipeline"], item["source"]))
        issues.append(
            {
                **item,
                "origin": "pipeline",
                "service_id": PIPELINE_SERVICES.get(item["pipeline"], item["pipeline"]),
                "severity": "error",
                "error_type": item["status"],
                "run_id": str(item["run_id"]),
                "last_success": success,
                "recovery": "later_success"
                if success and success > item["last_seen"]
                else "unconfirmed",
            }
        )
    for item in sources:
        checked = item.get("checked_at")
        state = source_state(item, now)
        if not state["problem"]:
            continue
        stale = state["stale"]
        source_service = state["service_id"]
        source_severity = "error" if state["severe"] else "warning"
        if service_id and source_service != service_id:
            continue
        if severity != "all" and severity != source_severity:
            continue
        issues.append(
            {
                "origin": "source",
                "service_id": source_service,
                "source": item["source"],
                "pipeline": None,
                "severity": source_severity,
                "error_type": "stale_source" if stale else item["status"],
                "message": item["detail"],
                "first_seen": None,
                "last_seen": checked,
                "occurrences": None,
                "recovery": "source_stale" if stale else "source_failure",
                "last_success": None,
            }
        )
    issues.sort(
        key=lambda item: item["last_seen"] or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    has_more = len(issues) > limit or len(telemetry) > limit or len(runs) > limit
    for item in issues:
        item["message"] = scrub_text(
            str(item.get("message") or "No error text recorded"), limit=4000
        )[0]
    for item in sources:
        item["detail"] = scrub_text(str(item.get("detail") or ""), limit=4000)[0]
    return {
        "generated_at": now,
        "since": since,
        "issues": issues[:limit],
        "has_more": has_more,
        "sources": sources,
        "unavailable": unavailable,
    }
