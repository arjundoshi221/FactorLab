"""One-shot full historical backfill driver.

Examples:
    # Run only Phase 1 (reference dims)
    python scripts/us/political/us_political_backfill.py --phase 1

    # Phase 2 with House Clerk full + Senate eFD recent month
    python scripts/us/political/us_political_backfill.py --phase 2 \
        --senate-efd-from 2026-04-01 --senate-efd-to 2026-04-30

    # Phase 3 with select tickers
    python scripts/us/political/us_political_backfill.py --phase 3 \
        --contract-tickers LMT,RTX,NOC,PLTR --contract-fy 2025

    # All four phases
    python scripts/us/political/us_political_backfill.py --all
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Path layout: scripts/us/political/<file>.py (multi-vendor orchestrator)
#   parents[0]=political, [1]=us, [2]=scripts, [3]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _parse_year_range(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s)]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    )
    ap = argparse.ArgumentParser(description="Political-data backfill driver")
    ap.add_argument("--phase", type=int, action="append", default=None,
                    help="Phase to run (1=dims, 2=trades, 3=conjunction, 4=verify). Repeatable.")
    ap.add_argument("--all", action="store_true", help="Run phases 1-4")

    # Phase 2 options
    ap.add_argument("--house-clerk-years", default="2013-2026",
                    help="Year or year range for House Clerk PTRs (PTRs start 2013 — STOCK Act)")
    ap.add_argument("--house-clerk-limit", type=int, default=None,
                    help="Cap House Clerk PTRs (testing)")
    ap.add_argument("--senate-efd-from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--senate-efd-to", default=None, help="YYYY-MM-DD")
    ap.add_argument("--senate-efd-headed", action="store_true",
                    help="Show browser (debugging)")
    ap.add_argument("--senate-efd-max-filings", type=int, default=None)
    ap.add_argument("--skip-senate-efd", action="store_true")

    # Phase 3 options — contracts (USASpending + Finnhub)
    ap.add_argument("--contract-tickers", default=None,
                    help="Comma-separated tickers, e.g. LMT,RTX. Default: all known")
    ap.add_argument("--contract-fy", type=int, default=2025)
    ap.add_argument("--contract-fy-range", default=None, help="e.g. 2014-2026")
    ap.add_argument("--lda-years", default=None, help="Year or year range for LDA")
    ap.add_argument("--lda-max-pages-per-year", type=int, default=None)
    ap.add_argument("--skip-finnhub", action="store_true")
    ap.add_argument("--skip-usaspending", action="store_true")
    ap.add_argument("--skip-lda", action="store_true")
    # Phase 3 options — FEC
    ap.add_argument("--fec-cycles", default=None,
                    help="Comma-separated FEC cycles, e.g. 2018,2020,2022,2024")
    ap.add_argument("--fec-include-unresolved-pacs", action="store_true",
                    help="Mode A: also iterate corp PACs without a resolved ticker")
    ap.add_argument("--fec-skip-mode-a", action="store_true")
    ap.add_argument("--fec-skip-mode-b", action="store_true")
    ap.add_argument("--skip-fec", action="store_true")
    # Phase 3 options — Congress.gov
    ap.add_argument("--congress", type=int, action="append", default=None,
                    help="Congress number (repeat for multiple). Default: 117 118 119")
    ap.add_argument("--congress-skip-deep", action="store_true",
                    help="Skip Pass C (cosponsors+committees+actions)")
    ap.add_argument("--congress-skip-hearings", action="store_true")
    ap.add_argument("--skip-congress", action="store_true")

    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        phases = [1, 2, 3, 4]
    elif args.phase:
        phases = sorted(set(args.phase))
    else:
        ap.error("specify --phase N (repeatable) or --all")

    house_clerk_years = _parse_year_range(args.house_clerk_years)
    contract_fy_range = _parse_year_range(args.contract_fy_range) if args.contract_fy_range else None
    lda_years = _parse_year_range(args.lda_years) if args.lda_years else None
    contract_tickers = (
        [t.strip().upper() for t in args.contract_tickers.split(",")]
        if args.contract_tickers else None
    )
    fec_cycles = (
        [int(c.strip()) for c in args.fec_cycles.split(",")]
        if args.fec_cycles else None
    )

    from factorlab.storage.db import get_engine
    from factorlab.countries.us.political.orchestrator import run_all

    results = run_all(
        get_engine(),
        phases=phases,
        # Phase 2
        house_clerk_years=house_clerk_years,
        house_clerk_limit=args.house_clerk_limit,
        senate_efd_from=args.senate_efd_from,
        senate_efd_to=args.senate_efd_to,
        senate_efd_headless=not args.senate_efd_headed,
        senate_efd_max_filings=args.senate_efd_max_filings,
        skip_senate_efd=args.skip_senate_efd,
        # Phase 3
        contract_tickers=contract_tickers,
        contract_fy=args.contract_fy,
        contract_fy_range=contract_fy_range,
        lda_years=lda_years,
        lda_max_pages_per_year=args.lda_max_pages_per_year,
        skip_finnhub=args.skip_finnhub,
        skip_usaspending=args.skip_usaspending,
        skip_lda=args.skip_lda,
        # FEC
        fec_cycles=fec_cycles,
        fec_include_unresolved_pacs=args.fec_include_unresolved_pacs,
        fec_skip_mode_a=args.fec_skip_mode_a,
        fec_skip_mode_b=args.fec_skip_mode_b,
        skip_fec=args.skip_fec,
        # Congress.gov
        congress_numbers=args.congress,
        congress_skip_deep=args.congress_skip_deep,
        congress_skip_hearings=args.congress_skip_hearings,
        skip_congress=args.skip_congress,
        # Common
        dry_run=args.dry_run,
    )
    print("\n" + "=" * 72)
    print("BACKFILL SUMMARY")
    print("=" * 72)
    for r in results:
        print(f"\n--- Phase {r.phase}: {r.name} (sources: {r.sources_run}) ---")
        for note in r.notes:
            print(f"  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
