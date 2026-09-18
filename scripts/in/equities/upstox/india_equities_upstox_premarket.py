"""india_equities_upstox_premarket — Daily token + instruments + universe prep.

Pattern:    country_domain_vendor_action
Country:    India (NSE; BSE pending)
Domain:     equities (cash; futures handled by the live script)
Vendor:     Upstox V3 (OAuth code-grant, daily token rotation via Railway)
Action:     premarket (one-shot, runs before market open)

Run model:  One-shot — exits 0 on success, non-zero on failure.
Schedule:   Task Scheduler `FactorLab-IndiaEquities-Upstox-PreMarket`, daily 06:00 IST.
Duration:   <2 min on green path.
Output:     Fresh instruments master in data/upstox/instruments/, universe CSVs in data/in/universes/.
Failure:    factorlab.shared.notify.notify(severity='fail') on any step failure;
            severity='fatal' on uncaught crash via supervised().

Flow:
  1. XBOM trading-day check (--force overrides)
  2. Auth: local .token → Railway /token (preferred) → interactive (last resort)
  3. Validate token against Upstox /profile
  4. Refresh instruments master (NSE)
  5. Build universes (demo, nifty50, nifty100, fo_eligible, nifty500, ...)

Exit codes:
   0 = success
   1 = auth failure
   2 = instruments download failure
   3 = token validation failure
   4 = universe build failure
  10 = not a trading day (expected; not an error)
  99 = unexpected error

Usage:
  python scripts/in/equities/upstox/india_equities_upstox_premarket.py
  python scripts/in/equities/upstox/india_equities_upstox_premarket.py --force       # ignore holiday check
  python scripts/in/equities/upstox/india_equities_upstox_premarket.py --auth-only   # skip instruments

Docs:
  docs/data-sources/india/upstox.md          — vendor integration
  docs/operations/orchestrators.md            — schedule + failure modes
  docs/operations/windows-task-scheduler.md  — Task Scheduler registration
"""

import argparse
import os
import sys
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Project root ─────────────────────────────────────────────────────────────
# Path layout: scripts/in/equities/upstox/<file>.py
#   parents[0]=upstox, [1]=equities, [2]=in, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

from factorlab.shared.paths import raw_dir  # noqa: E402
from factorlab.shared.runtime import (  # noqa: E402
    ExitCode,
    MarketWindow,
    setup_logging,
    supervised,
)
from factorlab.countries.in_.equities.upstox.auth import ensure_token, validate_token  # noqa: E402
from factorlab.countries.in_.equities.upstox.instruments import refresh_all  # noqa: E402
from factorlab.countries.in_.equities.upstox.universes import build_universes  # noqa: E402

log = setup_logging("india_equities_upstox_premarket")

# ── Config ───────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")
MARKET = MarketWindow(
    calendar_key="XBOM",
    open_time=dt_time(9, 15),
    close_time=dt_time(15, 30),
    tz=IST,
)
INSTRUMENTS_CACHE_DIR = raw_dir("upstox_instruments")
INSTRUMENTS_EXCHANGES = ("NSE",)
UNIVERSES_DIR = PROJECT_ROOT / "data" / "in" / "universes"


# ── Helpers ──────────────────────────────────────────────────────────────────


def send_failure_notification(
    message: str,
    *,
    subject: str = "Pre-market setup FAILED",
    context: dict | None = None,
) -> None:
    """Route failure alert through factorlab.shared.notify.

    Backends chosen via ``FACTORLAB_NOTIFY_BACKEND`` (default ``outlook``);
    the JSONL audit record is always-on. Structured metadata
    (script/frequency/vendor/domain/country) is rendered as a table in the
    HTML email and persisted as columns in ``logs/notify.jsonl`` —
    pass ``context`` for per-call extras (exit code, last error, etc.).
    """
    from factorlab.shared.notify import notify  # local import
    notify(
        subject=subject,
        body=message,
        severity="fail",
        source="india_equities_upstox_premarket",
        script="scripts/in/equities/upstox/india_equities_upstox_premarket.py",
        frequency="daily 06:00 IST",
        vendor="Upstox",
        domain="equities",
        country="IN",
        context=context,
    )


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="India pre-market: token sync + instruments + validation"
    )
    parser.add_argument("--force", action="store_true", help="Run even on non-trading days")
    parser.add_argument("--auth-only", action="store_true", help="Skip instruments download")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("india_equities_upstox_premarket starting")
    log.info("=" * 60)

    load_dotenv(find_dotenv(usecwd=True))

    # 1. Authenticate (always — token refresh runs on non-trading days too)
    #    ensure_token() tries: local .token → .env → Railway auth server → browser login
    try:
        token = ensure_token(interactive=False)
        log.info("Auth succeeded")
    except Exception as exc:
        msg = f"Auth FAILED: {exc}"
        log.error(msg)
        send_failure_notification(f"[FactorLab] Pre-market auth failed:\n{msg}")
        return 1

    # 2. Validate token before doing any API work
    try:
        profile = validate_token(token)
        log.info("Token valid — user=%s", profile.get("user_name", "?"))
    except Exception as exc:
        msg = f"Token validation FAILED: {exc}"
        log.error(msg)
        send_failure_notification(f"[FactorLab] Token validation failed:\n{msg}")
        return 3

    # 3. Holiday check — skip the rest of the pre-market work on non-trading days
    if not args.force:
        if not MARKET.is_trading_day():
            log.info("Not a trading day (XBOM). Exit %d.", int(ExitCode.NOT_TRADING_DAY))
            return int(ExitCode.NOT_TRADING_DAY)
        log.info("Trading day confirmed (XBOM)")
    else:
        log.info("--force: skipping holiday check")

    # 4. Instruments + universes
    if not args.auth_only:
        try:
            result = refresh_all(INSTRUMENTS_CACHE_DIR, INSTRUMENTS_EXCHANGES)
            for exch, instruments in result.items():
                log.info("  %s: %d instruments", exch, len(instruments))
        except Exception as exc:
            msg = f"Instruments download FAILED: {exc}"
            log.error(msg)
            send_failure_notification(f"[FactorLab] Instruments download failed:\n{msg}")
            return 2

        # 5. Build universe CSVs from instruments
        try:
            nse_instruments = result["NSE"]
            universes = build_universes(nse_instruments, UNIVERSES_DIR)
            for name, count in universes.items():
                log.info("  Universe %s: %d symbols", name, count)
        except Exception as exc:
            msg = f"Universe build FAILED: {exc}"
            log.error(msg)
            send_failure_notification(f"[FactorLab] Universe build failed:\n{msg}")
            return 4
    else:
        log.info("--auth-only: skipping instruments + universes")

    log.info("=" * 60)
    log.info("india_equities_upstox_premarket completed successfully")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(supervised(
        main,
        name="india_equities_upstox_premarket",
        on_crash=lambda exc: send_failure_notification(
            f"[FactorLab] Pre-market crashed:\n{exc}"
        ),
    ))
