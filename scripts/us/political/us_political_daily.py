"""Political-data orchestrator — daily / weekly / verify-only.

Modes:
  --mode daily   (default, back-compat with prior cron-style invocations):
    legislators YAML refresh, House Clerk current year, Senate eFD last 7d,
    LDA current year. Designed to run ~15 min Mon-Sat. Failure of senate_efd
    (Akamai block from non-residential IP) is non-fatal; House Clerk is.

  --mode weekly:
    Daily steps + USASpending current FY + Finnhub contracts + full-year LDA
    + anomaly snapshot + verify_political audit. Sunday at 17:30 IST.

  --mode verify-only:
    Just runs verify_political + captures a metrics snapshot. No ingestion.

Exit codes:
  0  OK
  2  warn (non-fatal source failure or warn-level anomaly)
  3  fatal (DB connectivity / schema / fail-level anomaly)
  75 lock held (another orchestrator already running)

Concurrency: file lock at logs/.political.lock (12h stale threshold).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Path layout: scripts/us/political/<file>.py (multi-vendor orchestrator)
#   parents[0]=political, [1]=us, [2]=scripts, [3]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.countries.us.political._metrics import (  # noqa: E402
    append_metrics,
    capture_metrics,
    compare_metrics,
    load_previous_metrics,
    write_alerts,
)
from factorlab.shared.notify import notify  # noqa: E402
from factorlab.shared.runtime import (  # noqa: E402
    EXIT_FATAL,
    EXIT_LOCK_HELD,
    EXIT_OK,
    EXIT_WARN,
    RunState,
    acquire_lock,
    dated_log_dir,
    load_run_state,
    mark_fail,
    mark_ok,
    save_run_state,
    should_run,
)


def make_dated_log_dir(mode: str, *, base: Path | None = None) -> Path:
    """Per-mode log bundle: logs/political_<mode>_YYYYMMDD/."""
    return dated_log_dir(f"political_{mode}", base=base)


def toast(title: str, body: str) -> None:
    """Anomaly alert — fan out via shared notify (Outlook + JSONL)."""
    notify(subject=title, body=body, severity="warn",
           source="political_orchestrator")

# Where the per-source last-run record lives (used by --mode hourly to know
# what's still cooling down vs. eligible to retry).
RUN_STATE_PATH = Path("logs/political_run_state.json")


# ── source non-fatal allowlist ──────────────────────────────────────────────
# House Clerk failure → fatal (it's the spine; missing PTRs = silent gap).
# Everything else → warn (vendor outage, residential-IP block, key issue).
NON_FATAL_SOURCES = {
    "senate_efd", "lda", "usaspending", "finnhub_contracts",
    "fec", "congress_gov",
}


def _setup_logging(log_dir: Path, mode: str) -> logging.Logger:
    log_file = log_dir / f"orchestrator.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("factlab_political")
    log.info("=== political orchestrator: mode=%s log_dir=%s ===", mode, log_dir)
    return log


def _run_source(
    name: str,
    fn,
    kwargs: dict,
    log: logging.Logger,
    results: list[tuple[str, str, int, float, str | None]],
    state: dict[str, RunState] | None = None,
    *,
    success_cooldown_hours: float = 0.0,
) -> None:
    """Wrap one ingest call with timing + non-fatal classification + run-state.

    `state` is the dict loaded from RUN_STATE_PATH; mutated in place. When
    `success_cooldown_hours > 0` (hourly mode), the source is SKIPPED if it
    already succeeded recently OR is still in deferred cooldown.

    `RateLimitDeferred` from `_client._fetch` is caught specifically and
    recorded with the server's Retry-After. The next `--mode hourly` run after
    that timestamp will pick it back up.
    """
    # Lazily import here so the runner module stays loadable from anywhere
    from factorlab.countries.us.political._client import RateLimitDeferred

    if state is not None:
        eligible, reason = should_run(name, state,
                                      success_cooldown_hours=success_cooldown_hours)
        if not eligible:
            log.info("[%s] SKIP — %s", name, reason)
            results.append((name, "skip", 0, 0.0, reason))
            return

    started = datetime.now(timezone.utc)
    try:
        res = fn(**kwargs)
        n_inserted = (
            getattr(res, "trades_inserted", None)
            or getattr(res, "rows_inserted", None)
            or getattr(res, "inserted", None)
            or 0
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        log.info("[%s] OK  inserted=%s elapsed=%.1fs", name, n_inserted, elapsed)
        results.append((name, "ok", n_inserted, elapsed, None))
        if state is not None:
            mark_ok(name, state)
    except RateLimitDeferred as e:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        log.warning("[%s] DEFERRED — Retry-After=%.0fs (%s)",
                    name, e.retry_after_sec, e)
        if state is not None:
            mark_fail(name, state, retry_after_sec=e.retry_after_sec, error=str(e))
        # Deferred = warn (non-fatal), regardless of allowlist
        results.append((name, "warn", 0, elapsed, f"deferred {e.retry_after_sec:.0f}s"))
    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        tb = traceback.format_exc()
        if name in NON_FATAL_SOURCES:
            log.warning("[%s] FAIL (non-fatal): %s", name, e)
            log.debug(tb)
            results.append((name, "warn", 0, elapsed, str(e)))
            if state is not None:
                mark_fail(name, state, error=str(e))
        else:
            log.error("[%s] FAIL (FATAL): %s", name, e)
            log.error(tb)
            results.append((name, "fatal", 0, elapsed, str(e)))
            if state is not None:
                mark_fail(name, state, error=str(e))


def _aggregate_exit_code(
    results: list[tuple[str, str, int, float, str | None]],
    anomalies: list = None,
) -> int:
    if any(r[1] == "fatal" for r in results):
        return EXIT_FATAL
    if anomalies and any(getattr(a, "severity", None) == "fail" for a in anomalies):
        return EXIT_FATAL
    if any(r[1] == "warn" for r in results):
        return EXIT_WARN
    if anomalies and any(getattr(a, "severity", None) == "warn" for a in anomalies):
        return EXIT_WARN
    return EXIT_OK


# ── modes ──────────────────────────────────────────────────────────────────


def run_daily(args, log: logging.Logger, log_dir: Path,
              *, success_cooldown_hours: float = 0.0) -> int:
    from factorlab.storage.db import get_engine
    from factorlab.countries.us.political.legislators.ingest import ingest_legislators
    from factorlab.countries.us.political.house_clerk.ingest import ingest_house_clerk
    from factorlab.countries.us.political.lda.ingest import ingest_lda

    engine = get_engine()
    today = date.today()
    week_ago = today - timedelta(days=7)
    results: list[tuple[str, str, int, float, str | None]] = []
    state = load_run_state(RUN_STATE_PATH)

    _run_source("legislators", ingest_legislators,
                {"engine": engine, "dry_run": args.dry_run},
                log, results, state, success_cooldown_hours=success_cooldown_hours)
    _run_source("house_clerk", ingest_house_clerk,
                {"engine": engine, "years": [today.year], "dry_run": args.dry_run},
                log, results, state, success_cooldown_hours=success_cooldown_hours)

    if not args.skip_senate_efd:
        from factorlab.countries.us.political.senate_efd.ingest import ingest_senate_efd
        _run_source("senate_efd", ingest_senate_efd, {
            "engine": engine,
            "date_from": week_ago.isoformat(),
            "date_to": today.isoformat(),
            "headless": True,
            "dry_run": args.dry_run,
        }, log, results, state, success_cooldown_hours=success_cooldown_hours)

    _run_source("lda", ingest_lda,
                {"engine": engine, "years": [today.year], "dry_run": args.dry_run},
                log, results, state, success_cooldown_hours=success_cooldown_hours)

    # Congress.gov daily incremental — Pass A (list pull, idempotent on bill_uid)
    # + Pass B (detail enrichment for bills with NULL policy_area). No Pass C
    # daily — that's a 60K+ call workload, weekly only. Hearings refresh too —
    # ~2K hearings per active congress, ~5 min.
    if not args.skip_congress:
        from factorlab.countries.us.political.congress_gov.ingest import ingest_congress_gov
        # Restrict to the active congress (current calendar year falls in 119th
        # for 2025-26, 120th from 2027-28). Compute via simple lookup.
        active_congress = _active_congress(today.year)
        _run_source("congress_gov", ingest_congress_gov, {
            "engine": engine,
            "congresses": [active_congress],
            "do_list": True, "do_detail": True,
            "do_deep": False, "do_hearings": True,
            "dry_run": args.dry_run,
        }, log, results, state, success_cooldown_hours=success_cooldown_hours)

    save_run_state(RUN_STATE_PATH, state)
    return _aggregate_exit_code(results)


def _active_congress(year: int) -> int:
    """Map a calendar year to the active U.S. Congress number.
    113 = 2013-14, 114 = 2015-16, ... 119 = 2025-26, 120 = 2027-28.
    """
    return 113 + (year - 2013) // 2


def run_weekly(args, log: logging.Logger, log_dir: Path,
               *, success_cooldown_hours: float = 0.0) -> int:
    """Weekly mode = daily steps + heavier work + anomaly snapshot + verify."""
    daily_code = run_daily(args, log, log_dir,
                           success_cooldown_hours=success_cooldown_hours)

    from factorlab.storage.db import get_engine
    engine = get_engine()
    today = date.today()
    results: list[tuple[str, str, int, float, str | None]] = []
    # Reload run-state from disk (run_daily wrote to it as it went).
    state = load_run_state(RUN_STATE_PATH)

    if not args.skip_contracts:
        from factorlab.countries.us.political.usaspending.ingest import ingest_usaspending
        from factorlab.countries.us.political.finnhub_contracts.ingest import (
            ingest_finnhub_contracts,
        )
        _run_source("usaspending", ingest_usaspending,
                    {"engine": engine, "fy": today.year, "dry_run": args.dry_run},
                    log, results, state, success_cooldown_hours=success_cooldown_hours)
        _run_source("finnhub_contracts", ingest_finnhub_contracts,
                    {"engine": engine, "fy": today.year, "dry_run": args.dry_run},
                    log, results, state, success_cooldown_hours=success_cooldown_hours)

    # FEC current-cycle refresh — Mode A (corp PAC outflows) + Mode B (≥$1K
    # individuals). Restricts to the active 2-year cycle to keep weekly cost
    # bounded; full multi-cycle backfill is a one-shot via
    # `us_political_backfill.py`, not weekly.
    if not args.skip_fec:
        from factorlab.countries.us.political.fec.ingest import ingest_fec
        active_cycle = today.year if today.year % 2 == 0 else today.year + 1
        _run_source("fec", ingest_fec, {
            "engine": engine,
            "do_committees": True,
            "do_mode_a": True, "do_mode_b": True,
            "cycles": [active_cycle],
            "dry_run": args.dry_run,
        }, log, results, state, success_cooldown_hours=success_cooldown_hours)

    # Congress.gov weekly deep refresh — Pass C (cosponsors/committees/actions
    # for priority-policy bills). The daily run handles list+detail; weekly
    # gets the heavy sub-resource fetch.
    if not args.skip_congress:
        from factorlab.countries.us.political.congress_gov.ingest import ingest_congress_gov
        active_congress = _active_congress(today.year)
        _run_source("congress_gov_deep", ingest_congress_gov, {
            "engine": engine,
            "congresses": [active_congress],
            "do_list": False, "do_detail": False,
            "do_deep": True, "do_hearings": False,
            "dry_run": args.dry_run,
        }, log, results, state, success_cooldown_hours=success_cooldown_hours)

    # Anomaly snapshot
    log.info("[weekly] capturing metrics snapshot")
    metrics = capture_metrics(engine)
    metrics_path = Path("logs/political_metrics.jsonl")
    previous = load_previous_metrics(metrics_path)
    alerts = compare_metrics(metrics, previous)
    append_metrics(metrics, metrics_path)
    if alerts:
        alerts_path = Path("logs/political_alerts.jsonl")
        write_alerts(alerts, alerts_path)
        log.warning("[weekly] %d anomalies detected — see %s", len(alerts), alerts_path)
        for a in alerts:
            log.warning("  %s [%s]: %s", a.metric, a.severity, a.message)
        toast("FactorLab — political anomalies",
              f"{len(alerts)} alerts (see {alerts_path})")
    else:
        log.info("[weekly] no anomalies vs previous snapshot")

    # Verify pass — runs the existing audit script + tees output to log_dir
    if not args.skip_verify:
        verify_log = log_dir / "verify.log"
        log.info("[weekly] running verify_political → %s", verify_log)
        with verify_log.open("w", encoding="utf-8") as f:
            res = subprocess.run(
                [sys.executable, "scripts/verify_political.py"],
                stdout=f, stderr=subprocess.STDOUT,
            )
        log.info("[weekly] verify_political exit=%d", res.returncode)

    save_run_state(RUN_STATE_PATH, state)
    weekly_code = _aggregate_exit_code(results, anomalies=alerts)
    return max(daily_code, weekly_code)


def run_hourly(args, log: logging.Logger, log_dir: Path) -> int:
    """Hourly self-healing pass. Re-runs ONLY sources that:
      (a) are in deferred-cooldown that has now elapsed, OR
      (b) failed in the last 24h with no successful run since.

    Sources that succeeded within the last `--hourly-cooldown` (default 6h)
    are skipped — no point hammering APIs that just gave us fresh data.

    Designed for unattended Task Scheduler use: schedule every hour 24/7,
    and the gate inside `_run_source` keeps it cheap when there's nothing
    to do. Deferrals from a 429 with long Retry-After (e.g. FEC quota
    exhausted) get picked up automatically once the cooldown elapses.
    """
    log.info("[hourly] checking %s for eligible sources", RUN_STATE_PATH)
    state = load_run_state(RUN_STATE_PATH)
    if not state:
        log.info("[hourly] no run-state yet — first daily run will populate it")
        return EXIT_OK

    # Run the same daily source set, with a non-zero success cooldown so we
    # only retry what actually needs retrying. The shared `_run_source` gate
    # plus per-source state flags handle the rest.
    return run_daily(args, log, log_dir,
                     success_cooldown_hours=args.hourly_cooldown)


def run_verify_only(args, log: logging.Logger, log_dir: Path) -> int:
    """Run verify_political + capture a metrics snapshot. No ingestion."""
    from factorlab.storage.db import get_engine
    engine = get_engine()

    metrics = capture_metrics(engine)
    metrics_path = Path("logs/political_metrics.jsonl")
    previous = load_previous_metrics(metrics_path)
    alerts = compare_metrics(metrics, previous)
    append_metrics(metrics, metrics_path)

    log.info("[verify-only] metrics snapshot:")
    for k, v in metrics.__dict__.items():
        log.info("  %-30s %s", k, v)
    if alerts:
        log.warning("[verify-only] %d anomalies", len(alerts))
        for a in alerts:
            log.warning("  %s [%s]: %s", a.metric, a.severity, a.message)
        write_alerts(alerts, Path("logs/political_alerts.jsonl"))

    verify_log = log_dir / "verify.log"
    with verify_log.open("w", encoding="utf-8") as f:
        res = subprocess.run(
            [sys.executable, "scripts/verify_political.py"],
            stdout=f, stderr=subprocess.STDOUT,
        )
    log.info("[verify-only] verify_political exit=%d", res.returncode)
    return _aggregate_exit_code([], anomalies=alerts)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="Political-data orchestrator")
    ap.add_argument("--mode",
                    choices=["daily", "weekly", "hourly", "verify-only"],
                    default="daily")
    ap.add_argument("--hourly-cooldown", type=float, default=6.0,
                    help="Hours since last success before --mode hourly retries "
                         "a source (default: 6.0)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-senate-efd", action="store_true",
                    help="Skip Playwright (use on non-residential hosts)")
    ap.add_argument("--skip-contracts", action="store_true",
                    help="Skip USASpending + Finnhub (heavy; weekly default = on)")
    ap.add_argument("--skip-congress", action="store_true",
                    help="Skip Congress.gov bills + hearings ingest")
    ap.add_argument("--skip-fec", action="store_true",
                    help="Skip FEC current-cycle refresh (weekly only)")
    ap.add_argument("--skip-verify", action="store_true",
                    help="Skip the verify_political audit step (weekly only)")
    ap.add_argument("--lock-file", type=Path,
                    default=Path("logs/.political.lock"))
    args = ap.parse_args()

    log_dir = make_dated_log_dir(args.mode)
    log = _setup_logging(log_dir, args.mode)

    try:
        with acquire_lock(args.lock_file):
            if args.mode == "daily":
                return run_daily(args, log, log_dir)
            elif args.mode == "hourly":
                return run_hourly(args, log, log_dir)
            elif args.mode == "weekly":
                return run_weekly(args, log, log_dir)
            elif args.mode == "verify-only":
                return run_verify_only(args, log, log_dir)
    except SystemExit as e:
        # acquire_lock raises SystemExit on overlap
        log.error(str(e))
        return EXIT_LOCK_HELD
    except Exception as e:
        log.error("orchestrator FATAL: %s", e)
        log.error(traceback.format_exc())
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
