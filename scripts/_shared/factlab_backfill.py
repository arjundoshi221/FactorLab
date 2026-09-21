"""factlab_backfill — CLI dispatcher for source-specific Backfillers.

Routes ``--source <name>`` to the registered :class:`Backfiller` and runs
``plan()`` (always) then ``run(plan, dry_run=...)`` (unless ``--dry-run``).

Examples::

    python scripts/_shared/factlab_backfill.py --list
    python scripts/_shared/factlab_backfill.py --source eodhd --dry-run
    python scripts/_shared/factlab_backfill.py --source eodhd \\
        --since 2024-01-01 --until 2024-01-31

Source-specific Backfillers are auto-registered when their module is
imported. The dispatcher imports each known source up-front so ``--list``
shows the full menu. New sources add an import line below + a
``register_backfiller(...)`` call in their backfill module.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.shared.ingest.backfill import (  # noqa: E402
    BackfillPlan,
    BackfillReport,
    get_backfiller,
    list_backfillers,
)
from factorlab.shared.runtime import (  # noqa: E402
    ExitCode,
    setup_logging,
    supervised,
)

# --- Source registrations (importing each module registers its Backfiller) --
#
# Add an import line per source. The import itself triggers
# ``register_backfiller(...)`` at module load.
try:  # pragma: no cover - import side effect
    import factorlab.countries.us.equities.eodhd.backfill  # noqa: F401
except Exception as e:  # pragma: no cover
    logging.getLogger(__name__).warning("eodhd backfill import failed: %s", e)


log = setup_logging("factlab_backfill")


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise SystemExit(f"--since / --until must be YYYY-MM-DD: {e}")


def _print_plan(plan: BackfillPlan) -> None:
    log.info("=== plan ===")
    log.info("source: %s", plan.source)
    log.info("units : %d", plan.unit_count)
    if plan.estimated_cost:
        log.info("est.  : %s", plan.estimated_cost)
    for n in plan.notes:
        log.info("  note: %s", n)
    if plan.unit_count and plan.unit_count <= 20:
        for u in plan.units:
            log.info("  unit: %s", u)


def _print_report(report: BackfillReport) -> None:
    log.info("=== report ===")
    for k, v in asdict(report).items():
        if k in ("errors", "notes"):
            for item in v:
                log.info("  %s: %s", k, item)
        else:
            log.info("  %-15s %s", k, v)


def main() -> int:
    p = argparse.ArgumentParser(
        description="FactorLab backfill dispatcher",
    )
    p.add_argument(
        "--source",
        help="source code (use --list to see available)",
    )
    p.add_argument("--since", help="YYYY-MM-DD start date (inclusive)")
    p.add_argument("--until", help="YYYY-MM-DD end date (inclusive)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print plan only; do not run",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="list registered Backfillers and exit",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit final BackfillReport as JSON to stdout",
    )
    args = p.parse_args()

    if args.list:
        names = list_backfillers()
        if not names:
            log.info("(no Backfillers registered)")
            return int(ExitCode.OK)
        log.info("registered Backfillers:")
        for n in names:
            log.info("  %s", n)
        return int(ExitCode.OK)

    if not args.source:
        log.error("--source is required (try --list to see available)")
        return int(ExitCode.FATAL)

    backfiller = get_backfiller(args.source)
    if backfiller is None:
        log.error("no Backfiller registered for source=%r", args.source)
        log.info("registered: %s", list_backfillers())
        return int(ExitCode.FATAL)

    since = _parse_date(args.since)
    until = _parse_date(args.until)

    plan = backfiller.plan(since=since, until=until)
    _print_plan(plan)

    if args.dry_run:
        log.info("dry-run -- exiting before run()")
        return int(ExitCode.OK)

    from factorlab.shared.notify import notify  # local import — avoid cycles

    report = backfiller.run(plan, dry_run=False, notify_fn=notify)
    _print_report(report)

    if args.json:
        sys.stdout.write(json.dumps(asdict(report), default=str) + "\n")

    if not report.ok:
        return int(ExitCode.WARN)
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(supervised(main, name="factlab_backfill"))
