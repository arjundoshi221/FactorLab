"""Status verdict rules: failures never read as healthy."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from factorlab.components.api.app import app, get_catalog_service
from factorlab.components.api.instrument_matrix import CACHE as MATRIX_CACHE
from factorlab.components.api.monitoring import repository
from factorlab.components.api.monitoring_summary import (
    CACHE,
    Check,
    broker_checks,
    build_summary,
    coverage_checks,
    pipeline_checks,
    service_checks,
    source_checks,
    summarize,
)

# Thu 2026-09-24 21:00 UTC = 17:00 New York, after the 16:30 default slot.
NOW = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)


def check(severity):
    return Check(
        id=severity,
        area="services",
        severity=severity,
        title="",
        detail="",
        href="/",
        link_label="",
    )


def test_verdict_rolls_up_by_worst_severity():
    assert summarize([check("ok"), check("info")], NOW).verdict == "ok"
    assert summarize([check("ok"), check("unknown")], NOW).verdict == "unknown"
    assert summarize([check("unknown"), check("warning")], NOW).verdict == "attention"
    summary = summarize([check("warning"), check("critical"), check("ok")], NOW)
    assert summary.verdict == "critical"
    assert [c.severity for c in summary.checks] == ["critical", "warning", "ok"]
    assert summary.counts == {"critical": 1, "warning": 1, "unknown": 0, "info": 0, "ok": 1}


def test_service_rules():
    def svc(sid, status, **extra):
        return {"service_id": sid, "status": status, "registered": True, **extra}

    checks = service_checks(
        {
            "monitor_offline": False,
            "services": [
                svc("a", "down"),
                svc("b", "late"),
                svc("c", "up", err_count_1h=3),
                svc("d", "unknown"),
                svc("e", "up", registered=False),
                svc("f", "up"),
                svc("g", "up"),
            ],
        }
    )
    by_id = {c.id: c.severity for c in checks}
    assert by_id == {
        "services:a": "critical",
        "services:b": "warning",
        "services:c": "warning",
        "services:d": "info",
        "services:e": "warning",
        "services:ok": "ok",
    }
    assert next(c for c in checks if c.id == "services:ok").count == 2
    assert next(c for c in checks if c.id == "services:a").href == "/monitoring#service-a"
    offline = service_checks({"monitor_offline": True, "services": [svc("a", "up")]})
    assert [(c.id, c.severity) for c in offline] == [("services:monitor", "critical")]


def test_source_and_pipeline_rules():
    def source(**kw):
        base: dict[str, object] = {
            "country_code": "US",
            "source": "schwab",
            "status": "ready",
            "detail": "",
            "checked_at": NOW,
            "problem": False,
            "stale": False,
            "severe": False,
        }
        return SimpleNamespace(**{**base, **kw})

    checks = source_checks(
        [
            source(),
            source(source="universe", status="auth_required", problem=True, severe=True),
            source(source="ibkr", status="ready", problem=True, stale=True),
        ]
    )
    assert {c.id: c.severity for c in checks} == {
        "sources:US:universe": "critical",
        "sources:US:ibkr": "warning",
        "sources:ok": "ok",
    }
    assert "reporting overdue" in next(c for c in checks if c.id == "sources:US:ibkr").title

    def pipe(pid, status, last_status: str | None = "success", per_symbol=False):
        return SimpleNamespace(
            id=pid,
            label=pid,
            status=status,
            status_reason="why",
            last_status=last_status,
            last_started_at=NOW,
            per_symbol_runs=per_symbol,
        )

    checks = pipeline_checks(
        [
            pipe("a", "healthy"),
            pipe("b", "not_expected"),
            pipe("c", "missing", None),
            pipe("d", "attention", "failed"),
            pipe("e", "attention", "failed", per_symbol=True),
            pipe("f", "attention", "success"),
        ]
    )
    assert {c.id: c.severity for c in checks} == {
        "pipelines:c": "critical",
        "pipelines:d": "critical",
        "pipelines:e": "warning",
        "pipelines:f": "warning",
        "pipelines:ok": "ok",
    }


def test_coverage_rules_do_not_treat_unmeasured_feeds_as_failures():
    def feed(**kw):
        base: dict[str, object] = {
            "country_code": "US",
            "source": "schwab",
            "resolution": "1m",
            "trade_date": NOW.date(),
            "missing": 0,
            "affected_series": 0,
            "unreported_series": 0,
            "active_series": 10,
            "checked_at": NOW,
        }
        return {**base, **kw}

    checks = coverage_checks(
        [
            feed(),
            feed(resolution="1d", missing=12, affected_series=3),
            feed(country_code="IN", source="upstox", trade_date=None),
        ]
    )
    by_id = {c.id: c for c in checks}
    assert by_id["coverage:US-schwab-1d"].severity == "warning"
    assert "12 missing bars across 3 tickers" in by_id["coverage:US-schwab-1d"].detail
    assert by_id["coverage:IN-upstox-1m"].severity == "info"
    assert by_id["coverage:ok"].count == 1


def test_broker_rules_follow_the_snapshot_schedule(monkeypatch):
    monkeypatch.delenv("IBKR_SNAPSHOT_TIMES", raising=False)
    accounts = [
        {"broker_code": "ibkr", "account_id": "U1", "user_id": "u", "simulated": False},
        {"broker_code": "ibkr", "account_id": "U2", "user_id": None, "simulated": False},
        {"broker_code": "ibkr", "account_id": "U3", "user_id": None, "simulated": False},
        {"broker_code": "ibkr", "account_id": "SIM_1", "user_id": "u", "simulated": True},
        {"broker_code": "ibkr", "account_id": "U4", "simulated": False, "active": False},
    ]
    snapshots = {
        ("ibkr", "U1"): NOW - timedelta(minutes=29),  # the 16:30 slot
        ("ibkr", "U2"): NOW - timedelta(days=1),  # missed today's slots
    }
    late = NOW + timedelta(minutes=45)  # 17:45 New York: 16:30 is past its grace
    checks = {c.id: c for c in broker_checks(accounts, snapshots, late)}
    assert checks["broker:ok"].count == 1
    assert checks["broker:ibkr:U2"].title.endswith("snapshot missed")
    assert checks["broker:ibkr:U3"].title.endswith("no snapshot in 14 days")
    assert "broker:ibkr:SIM_1" not in checks and "broker:ibkr:U4" not in checks
    assert checks["broker:ibkr:U2"].href == "/portfolio?account=%5B%22ibkr%22%2C%22U2%22%5D"


def test_failed_gatherers_become_unknown_never_ok():
    def query(sql, **kwargs):
        raise RuntimeError("database down")

    class Catalog:
        def pipelines(self):
            raise RuntimeError("catalog down")

    MATRIX_CACHE.clear()
    summary = build_summary(SimpleNamespace(query=query), Catalog(), NOW)
    assert summary.verdict == "unknown"
    assert {c.area for c in summary.checks} == {
        "services",
        "sources",
        "pipelines",
        "coverage",
        "broker",
        "tickers",
        "tagging",
    }
    assert all(c.severity == "unknown" for c in summary.checks)


def test_summary_endpoint_is_read_only_and_cached():
    queries = []

    def query(sql, **kwargs):
        queries.append((sql, kwargs))
        assert kwargs.get("settings", {}).get("readonly") == 2
        return SimpleNamespace(column_names=[], result_rows=[])

    catalog = SimpleNamespace(
        pipelines=lambda: SimpleNamespace(sources=[], pipelines=[], generated_at=NOW)
    )
    CACHE.clear()
    MATRIX_CACHE.clear()
    app.dependency_overrides[repository] = lambda: SimpleNamespace(query=query)
    app.dependency_overrides[get_catalog_service] = lambda: catalog
    try:
        client = TestClient(app)
        response = client.get("/hub/api/v1/monitoring/summary")
        assert response.status_code == 200
        data = response.json()
        # No monitor row means the watcher is offline: critical, never "all good".
        assert data["verdict"] == "critical"
        assert data["checks"][0]["id"] == "services:monitor"
        calls = len(queries)
        assert client.get("/hub/api/v1/monitoring/summary").status_code == 200
        assert len(queries) == calls
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


def test_ticker_checks_link_to_the_filtered_matrix():
    from datetime import date

    from factorlab.components.api.monitoring_summary import ticker_checks

    checks = ticker_checks(
        {
            "sessions": {"US": date(2026, 9, 23), "IN": date(2026, 9, 24)},
            "lagging": {"US": {"a", "b"}, "IN": set()},
        }
    )
    by_id = {c.id: c for c in checks}
    assert by_id["tickers:US"].severity == "warning"
    assert by_id["tickers:US"].count == 2
    assert by_id["tickers:US"].href == "/market-data?view=matrix&attention=bars&country=US"
    assert by_id["tickers:IN"].severity == "ok"


def test_untagged_orders_are_a_note_that_links_to_the_ledger():
    from factorlab.components.api.monitoring_summary import tagging_checks

    checks = tagging_checks(
        [
            {"user_id": "u-1", "display_name": "Jai", "untagged": 3},
            {"user_id": "u-2", "display_name": "Other", "untagged": 0},
        ]
    )
    assert [(c.id, c.severity, c.count) for c in checks] == [("tagging:u-1", "info", 3)]
    assert checks[0].href == "/portfolio/trades?user_id=u-1&tag=untagged"
    assert summarize(checks, NOW).verdict == "ok"  # a to-do never degrades the verdict
