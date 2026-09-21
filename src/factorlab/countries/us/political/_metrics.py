"""Political-pipeline anomaly metrics — domain-specific (SQL hardcodes alt_political_us tables).

Lives in the political package because the SQL bodies and the threshold rules
are pure political-pipeline concerns. Cross-cutting orchestrator primitives
(lock, RunState, exit codes, dated log dirs) live in
:mod:`factorlab.shared.runtime`.

Used by ``scripts/us/political/daily.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


@dataclass
class Metrics:
    """8 metrics captured weekly (and persisted to JSONL for trend tracking)."""
    captured_at: str = ""
    total_trades: int = 0
    trades_last_7d: int = 0
    bioguide_resolve_rate: float = 0.0
    ticker_resolve_rate: float = 0.0
    tier2_unresolved_new: int = 0
    lda_filings_current_year: int = 0
    gov_contracts_current_fy: int = 0
    raw_archive_growth_7d: int = 0


def capture_metrics(engine: Engine) -> Metrics:
    """One snapshot of the political-pipeline state."""
    m = Metrics(captured_at=datetime.now(timezone.utc).isoformat())
    with engine.connect() as c:
        m.total_trades = c.execute(text(
            "SELECT count(*) FROM alt_political_us.legislator_trades"
        )).scalar() or 0
        m.trades_last_7d = c.execute(text("""
            SELECT count(*) FROM alt_political_us.legislator_trades
            WHERE ingested_at >= now() - interval '7 days'
        """)).scalar() or 0
        bg = c.execute(text("""
            SELECT count(bioguide_id)::float / NULLIF(count(*), 0)
            FROM alt_political_us.legislator_trades
        """)).scalar()
        m.bioguide_resolve_rate = float(bg) if bg is not None else 0.0
        tk = c.execute(text("""
            SELECT count(ticker)::float / NULLIF(count(*), 0)
            FROM alt_political_us.legislator_trades
        """)).scalar()
        m.ticker_resolve_rate = float(tk) if tk is not None else 0.0
        m.tier2_unresolved_new = c.execute(text("""
            SELECT count(*) FROM alt_political_us.legislator_trades
            WHERE bioguide_id IS NULL
              AND ingested_at >= now() - interval '7 days'
        """)).scalar() or 0
        m.lda_filings_current_year = c.execute(text("""
            SELECT count(*) FROM alt_political_us.lobbying_filings
            WHERE filing_year = EXTRACT(YEAR FROM now())::int
        """)).scalar() or 0
        m.gov_contracts_current_fy = c.execute(text("""
            SELECT count(*) FROM alt_political_us.gov_contracts
            WHERE action_date >= date_trunc('year', now())
        """)).scalar() or 0
        m.raw_archive_growth_7d = c.execute(text("""
            SELECT count(*) FROM audit.raw_archive
            WHERE fetched_at >= now() - interval '7 days'
        """)).scalar() or 0
    return m


@dataclass
class Alert:
    metric: str
    severity: str  # 'warn' | 'fail'
    message: str
    current: float | int | None = None
    previous: float | int | None = None


def compare_metrics(current: Metrics, previous: Metrics | None) -> list[Alert]:
    """Diff two snapshots; emit alerts on threshold breaches."""
    alerts: list[Alert] = []
    if previous is not None:
        if current.total_trades < previous.total_trades:
            alerts.append(Alert(
                "total_trades", "fail",
                f"trade count dropped {previous.total_trades} -> {current.total_trades}",
                current.total_trades, previous.total_trades,
            ))
        if (previous.lda_filings_current_year and
                current.lda_filings_current_year < previous.lda_filings_current_year):
            alerts.append(Alert(
                "lda_filings_current_year", "fail",
                "LDA filings dropped",
                current.lda_filings_current_year, previous.lda_filings_current_year,
            ))
        if (previous.gov_contracts_current_fy and
                current.gov_contracts_current_fy < previous.gov_contracts_current_fy):
            alerts.append(Alert(
                "gov_contracts_current_fy", "fail",
                "gov_contracts current FY dropped",
                current.gov_contracts_current_fy, previous.gov_contracts_current_fy,
            ))
        if previous.ticker_resolve_rate - current.ticker_resolve_rate > 0.02:
            alerts.append(Alert(
                "ticker_resolve_rate", "warn",
                f"ticker rate dropped >2pp: "
                f"{previous.ticker_resolve_rate:.3f} -> {current.ticker_resolve_rate:.3f}",
                current.ticker_resolve_rate, previous.ticker_resolve_rate,
            ))

    if current.bioguide_resolve_rate < 0.97 and current.total_trades:
        alerts.append(Alert(
            "bioguide_resolve_rate", "fail",
            f"bioguide rate {current.bioguide_resolve_rate:.3f} < 0.97",
            current.bioguide_resolve_rate, None,
        ))
    elif current.bioguide_resolve_rate < 0.99 and current.total_trades:
        alerts.append(Alert(
            "bioguide_resolve_rate", "warn",
            f"bioguide rate {current.bioguide_resolve_rate:.3f} < 0.99",
            current.bioguide_resolve_rate, None,
        ))

    if current.tier2_unresolved_new > 25:
        alerts.append(Alert(
            "tier2_unresolved_new", "warn",
            f"{current.tier2_unresolved_new} new unresolved-bioguide rows in 7d",
            current.tier2_unresolved_new, None,
        ))

    if current.raw_archive_growth_7d < 5:
        alerts.append(Alert(
            "raw_archive_growth_7d", "warn",
            f"only {current.raw_archive_growth_7d} new audit rows in 7d "
            f"(ingest may be broken)",
            current.raw_archive_growth_7d, None,
        ))

    return alerts


def append_metrics(metrics: Metrics, jsonl_path: Path) -> None:
    """Append a metrics snapshot to ``logs/political_metrics.jsonl``."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metrics.__dict__) + "\n")


def load_previous_metrics(jsonl_path: Path) -> Metrics | None:
    """Read the last line of the metrics JSONL (None if file empty/absent)."""
    if not jsonl_path.exists():
        return None
    last_line = None
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line
    if not last_line:
        return None
    try:
        d = json.loads(last_line)
        return Metrics(**{k: v for k, v in d.items()
                          if k in Metrics.__dataclass_fields__})
    except Exception as e:
        log.warning("[metrics] could not parse last metrics line: %s", e)
        return None


def write_alerts(alerts: list[Alert], jsonl_path: Path) -> None:
    """Append alerts as JSONL — never overwrite history."""
    if not alerts:
        return
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with jsonl_path.open("a", encoding="utf-8") as f:
        for a in alerts:
            f.write(json.dumps({
                "ts": ts,
                "metric": a.metric,
                "severity": a.severity,
                "message": a.message,
                "current": a.current,
                "previous": a.previous,
            }) + "\n")


__all__ = [
    "Metrics",
    "Alert",
    "capture_metrics",
    "compare_metrics",
    "append_metrics",
    "load_previous_metrics",
    "write_alerts",
]
