"""One status verdict for "is everything working", built from each panel's own evidence.

Every gatherer is independent: a failed read becomes an ``unknown`` check and never
an ``ok`` one, so a broken query cannot hide a broken pipeline.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import BaseModel

from factorlab.clickhouse import rows

from .instrument_matrix import ticker_lag
from .monitoring import coverage_feeds, registry, services_response
from .portfolio import PortfolioRepository
from .source_health import broker_snapshot_stale

Severity = Literal["critical", "warning", "info", "ok", "unknown"]
Verdict = Literal["ok", "attention", "critical", "unknown"]
Area = Literal["services", "sources", "pipelines", "coverage", "broker", "tickers", "tagging"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4}
SETTINGS = {"readonly": 2, "max_execution_time": 5}
CACHE_SECONDS = 15.0
SNAPSHOT_LOOKBACK = timedelta(days=14)


class Check(BaseModel):
    id: str
    area: Area
    severity: Severity
    title: str
    detail: str
    count: int | None = None
    observed_at: datetime | None = None
    href: str
    link_label: str


class Summary(BaseModel):
    generated_at: datetime
    verdict: Verdict
    counts: dict[str, int]
    checks: list[Check]


def verdict(checks: list[Check]) -> Verdict:
    severities = {check.severity for check in checks}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "attention"
    if "unknown" in severities:
        return "unknown"
    return "ok"


def summarize(checks: list[Check], now: datetime) -> Summary:
    checks = sorted(
        checks, key=lambda check: (SEVERITY_ORDER[check.severity], check.area, check.id)
    )
    counts = dict.fromkeys(SEVERITY_ORDER, 0)
    for check in checks:
        counts[check.severity] += 1
    return Summary(generated_at=now, verdict=verdict(checks), counts=counts, checks=checks)


def unavailable(area: Area, title: str, href: str) -> Check:
    return Check(
        id=f"{area}:unavailable",
        area=area,
        severity="unknown",
        title=f"{title} could not be checked",
        detail="The status read failed, so this area is not confirmed healthy.",
        href=href,
        link_label="Open details",
    )


def passing(area: Area, count: int, title: str, href: str) -> list[Check]:
    if not count:
        return []
    return [
        Check(
            id=f"{area}:ok",
            area=area,
            severity="ok",
            title=title,
            detail="",
            count=count,
            href=href,
            link_label="Open details",
        )
    ]


# services -------------------------------------------------------------------------------


def service_checks(services: dict[str, Any]) -> list[Check]:
    href = "/monitoring#monitoring-services-title"
    if services["monitor_offline"]:
        return [
            Check(
                id="services:monitor",
                area="services",
                severity="critical",
                title="Service monitor offline",
                detail="Heartbeat tracking is interrupted, so no service status can be trusted.",
                href=href,
                link_label="Open services",
            )
        ]
    checks: list[Check] = []
    healthy = 0
    for row in services["services"]:
        sid = str(row["service_id"])
        status = str(row.get("status") or "unknown")
        errors = int(row.get("err_count_1h") or 0)
        seen = row.get("last_seen_at")

        def check(severity: Severity, title: str, detail: str, sid: str = sid, seen: Any = seen):
            checks.append(
                Check(
                    id=f"services:{sid}",
                    area="services",
                    severity=severity,
                    title=title,
                    detail=detail,
                    observed_at=seen,
                    href=f"/monitoring#service-{sid}",
                    link_label="Open service",
                )
            )

        if status == "down":
            check("critical", f"{sid} is down", "No heartbeat within its deadline.")
        elif status == "late":
            check("warning", f"{sid} heartbeat is late", "The latest heartbeat is overdue.")
        elif not row.get("registered", True):
            check("warning", f"Unregistered sender {sid}", "Not in monitor-services.yaml.")
        elif status == "up" and errors:
            check("warning", f"{sid} reported {errors} errors in the last hour", "")
        elif status == "unknown":
            check("info", f"{sid} has not reported yet", "Waiting for its first monitored run.")
        elif status == "stopped":
            check("info", f"{sid} reported a deliberate stop", "")
        else:
            healthy += 1
    return checks + passing("services", healthy, f"{healthy} services reporting on time", href)


# sources and pipelines ------------------------------------------------------------------


def source_checks(sources: list[Any]) -> list[Check]:
    checks: list[Check] = []
    healthy = 0
    for source in sources:
        if not source.problem:
            healthy += 1
            continue
        state = "reporting overdue" if source.stale else source.status.replace("_", " ")
        checks.append(
            Check(
                id=f"sources:{source.country_code}:{source.source}",
                area="sources",
                severity="critical" if source.severe else "warning",
                title=f"{source.country_code} / {source.source}: {state}",
                detail=source.detail or "No source status detail was recorded.",
                observed_at=source.checked_at,
                href="/monitoring#monitoring-errors-title",
                link_label="Inspect errors",
            )
        )
    return checks + passing(
        "sources",
        healthy,
        f"{healthy} collection sources ready",
        "/monitoring#monitoring-errors-title",
    )


def pipeline_checks(pipelines: list[Any]) -> list[Check]:
    checks: list[Check] = []
    healthy = 0
    for pipeline in pipelines:
        if pipeline.status not in ("attention", "missing"):
            healthy += 1
            continue
        critical = pipeline.status == "missing" or (
            pipeline.last_status == "failed" and not pipeline.per_symbol_runs
        )
        checks.append(
            Check(
                id=f"pipelines:{pipeline.id}",
                area="pipelines",
                severity="critical" if critical else "warning",
                title=f"{pipeline.label} needs attention",
                detail=pipeline.status_reason,
                observed_at=pipeline.last_started_at,
                href=f"/monitoring#pipeline-{pipeline.id}",
                link_label="Open pipeline",
            )
        )
    return checks + passing(
        "pipelines",
        healthy,
        f"{healthy} pipelines on schedule",
        "/monitoring#monitoring-pipelines-title",
    )


# coverage ---------------------------------------------------------------------------------


def coverage_checks(feeds: list[dict[str, Any]]) -> list[Check]:
    checks: list[Check] = []
    healthy = 0
    for feed in feeds:
        key = f"{feed['country_code']}-{feed['source']}-{feed['resolution']}"
        label = f"{feed['country_code']} · {feed['source']} · {feed['resolution']}"
        missing = int(feed.get("missing") or 0)
        affected = int(feed.get("affected_series") or 0)
        unreported = int(feed.get("unreported_series") or 0)
        href = f"/monitoring#coverage-{key}"
        if feed.get("trade_date") is None:
            # Not every feed records session checks (India does not); absence is not failure.
            checks.append(
                Check(
                    id=f"coverage:{key}",
                    area="coverage",
                    severity="info",
                    title=f"{label}: no session coverage checks recorded",
                    detail="Completeness for this feed is not measured.",
                    count=int(feed.get("active_series") or 0) or None,
                    href=href,
                    link_label="Open coverage",
                )
            )
            continue
        if missing or unreported:
            parts = []
            if missing:
                parts.append(f"{missing:,} missing bars across {affected:,} tickers")
            if unreported:
                parts.append(f"{unreported:,} active tickers without a session check")
            checks.append(
                Check(
                    id=f"coverage:{key}",
                    area="coverage",
                    severity="warning",
                    title=f"{label}: incomplete session {feed['trade_date']}",
                    detail="; ".join(parts) + ".",
                    count=affected + unreported,
                    observed_at=feed.get("checked_at"),
                    href=href,
                    link_label="Open coverage",
                )
            )
        else:
            healthy += 1
    return checks + passing(
        "coverage",
        healthy,
        f"{healthy} feeds complete for their latest session",
        "/monitoring#monitoring-data-title",
    )


# tickers ----------------------------------------------------------------------------------


def ticker_checks(lag: dict[str, Any]) -> list[Check]:
    """Configured tickers with no bar for their exchange's latest completed session."""
    checks: list[Check] = []
    for country, session in sorted(lag["sessions"].items()):
        behind = len(lag["lagging"].get(country, ()))
        href = f"/market-data?view=matrix&attention=bars&country={country}"
        if behind:
            checks.append(
                Check(
                    id=f"tickers:{country}",
                    area="tickers",
                    severity="warning",
                    title=f"{behind:,} {country} tickers have no bars for {session}",
                    detail="At least one configured series has not fetched the latest completed session.",
                    count=behind,
                    href=href,
                    link_label="Show tickers",
                )
            )
        else:
            checks.append(
                Check(
                    id=f"tickers:{country}",
                    area="tickers",
                    severity="ok",
                    title=f"All configured {country} tickers fetched {session}",
                    detail="",
                    count=1,
                    href=href,
                    link_label="Open matrix",
                )
            )
    return checks


# trade tags -------------------------------------------------------------------------------

UNTAGGED_SQL = """SELECT toString(o.user_id) AS user_id, any(u.display_name) AS display_name,
    uniqExact(tuple(f.broker_code, f.account_id, f.perm)) AS untagged
FROM (SELECT broker_code, account_id, exec_id, argMax(perm_id, version) AS perm
      FROM broker.executions GROUP BY broker_code, account_id, exec_id) AS f
INNER JOIN ref.broker_account_owners AS o FINAL
    ON o.broker_code = f.broker_code AND o.account_id = f.account_id
LEFT JOIN ref.app_users AS u FINAL ON u.user_id = o.user_id
LEFT JOIN (
    SELECT broker_code, account_id, user_id, perm_id,
        argMax(isNotNull(theme_id), tuple(changed_at, event_id)) AS tagged
    FROM book.order_assignment_events GROUP BY broker_code, account_id, user_id, perm_id
) AS t ON t.broker_code = f.broker_code AND t.account_id = f.account_id
    AND t.user_id = o.user_id AND t.perm_id = f.perm
WHERE f.perm > 0 AND t.tagged = 0
GROUP BY o.user_id"""


def tagging_checks(rows_: list[dict[str, Any]]) -> list[Check]:
    """Untagged orders are a to-do, not a failure: they never change the verdict."""
    return [
        Check(
            id=f"tagging:{row['user_id']}",
            area="tagging",
            severity="info",
            title=f"{int(row['untagged']):,} untagged orders for {row.get('display_name') or 'a profile'}",
            detail="Tag them with a theme, strategy and trade name in the trade ledger.",
            count=int(row["untagged"]),
            href="/portfolio/trades?" + urlencode({"user_id": row["user_id"], "tag": "untagged"}),
            link_label="Tag trades",
        )
        for row in rows_
        if int(row["untagged"])
    ]


# broker snapshots -------------------------------------------------------------------------


def latest_broker_snapshots(client: Any, now: datetime) -> dict[tuple[str, str], datetime]:
    """Latest recorded snapshot per account from the run manifests (one grouped read)."""
    result = rows(
        client.query(
            "SELECT JSONExtractString(snapshot, 'broker_code') AS broker_code, "
            "JSONExtractString(snapshot, 'account_id') AS account_id, "
            "max(parseDateTime64BestEffort(JSONExtractString(snapshot, 'snapshot_time'), 3, 'UTC')) "
            "AS snapshot_time FROM meta.ingestion_runs FINAL "
            "ARRAY JOIN JSONExtractArrayRaw(metadata_json, 'broker_snapshots') AS snapshot "
            "WHERE pipeline = 'ibkr_broker_snapshot' AND status != 'running' "
            "AND started_at >= {since:DateTime64(3, 'UTC')} "
            "GROUP BY broker_code, account_id",
            parameters={"since": now - SNAPSHOT_LOOKBACK},
            settings=SETTINGS,
        )
    )
    return {(str(r["broker_code"]), str(r["account_id"])): r["snapshot_time"] for r in result}


def account_href(account: dict[str, Any]) -> str:
    query = {
        "account": json.dumps(
            [account["broker_code"], account["account_id"]], separators=(",", ":")
        )
    }
    if account.get("user_id"):
        query = {"user_id": str(account["user_id"]), **query}
    return "/portfolio?" + urlencode(query)


def broker_checks(
    accounts: list[dict[str, Any]], snapshots: dict[tuple[str, str], datetime], now: datetime
) -> list[Check]:
    checks: list[Check] = []
    healthy = 0
    for account in accounts:
        # Simulated accounts are ingested on demand and paused accounts are not expected.
        if account.get("simulated") or account.get("active") is False:
            continue
        key = (str(account["broker_code"]), str(account["account_id"]))
        stamp = snapshots.get(key)
        if stamp is not None and not broker_snapshot_stale(stamp, now):
            healthy += 1
            continue
        name = f"{key[0].upper()} {key[1]}"
        checks.append(
            Check(
                id=f"broker:{key[0]}:{key[1]}",
                area="broker",
                severity="warning",
                title=f"{name}: snapshot missed" if stamp else f"{name}: no snapshot in 14 days",
                detail="The latest scheduled broker snapshot was not recorded."
                if stamp
                else "No broker snapshot run recorded this account recently.",
                observed_at=stamp,
                href=account_href(account),
                link_label="Open account",
            )
        )
    return checks + passing(
        "broker", healthy, f"{healthy} broker accounts up to date", "/portfolio"
    )


# assembly ---------------------------------------------------------------------------------

Gatherer = Callable[[], list[Check]]


def collect(gatherers: list[tuple[Area, str, str, Gatherer]]) -> list[Check]:
    checks: list[Check] = []
    for area, title, href, gather in gatherers:
        try:
            checks.extend(gather())
        except Exception:  # noqa: BLE001 - one failed read must not hide other evidence
            checks.append(unavailable(area, title, href))
    return checks


def build_summary(client: Any, catalog: Any, now: datetime | None = None) -> Summary:
    now = now or datetime.now(UTC)
    pipelines: dict[str, Any] = {}

    def catalog_pipelines() -> Any:
        if "value" not in pipelines:
            pipelines["value"] = catalog.pipelines()
        return pipelines["value"]

    def services() -> list[Check]:
        items = rows(client.query("SELECT * FROM meta.service_status FINAL", settings=SETTINGS))
        return service_checks(services_response(items, registry(), now))

    def broker() -> list[Check]:
        accounts = PortfolioRepository(client).accounts()
        return broker_checks(accounts, latest_broker_snapshots(client, now), now)

    return summarize(
        collect(
            [
                ("services", "Service health", "/monitoring#monitoring-services-title", services),
                (
                    "sources",
                    "Collection sources",
                    "/monitoring#monitoring-errors-title",
                    lambda: source_checks(catalog_pipelines().sources),
                ),
                (
                    "pipelines",
                    "Ingestion pipelines",
                    "/monitoring#monitoring-pipelines-title",
                    lambda: pipeline_checks(catalog_pipelines().pipelines),
                ),
                (
                    "coverage",
                    "Data completeness",
                    "/monitoring#monitoring-data-title",
                    lambda: coverage_checks(coverage_feeds(client, now)),
                ),
                ("broker", "Broker snapshots", "/portfolio", broker),
                (
                    "tagging",
                    "Trade tags",
                    "/portfolio/trades",
                    lambda: tagging_checks(rows(client.query(UNTAGGED_SQL, settings=SETTINGS))),
                ),
                (
                    "tickers",
                    "Ticker data",
                    "/market-data?view=matrix",
                    lambda: ticker_checks(ticker_lag(client, now)),
                ),
            ]
        ),
        now,
    )


class SummaryCache:
    def __init__(self, seconds: float = CACHE_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._value: tuple[float, Summary] | None = None

    def get(self, build: Callable[[], Summary]) -> Summary:
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


CACHE = SummaryCache()
