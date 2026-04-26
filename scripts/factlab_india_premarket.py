"""factlab_india_premarket — Daily token refresh + instruments download.

Schedule: Run manually each morning before market (or via Task Scheduler at 06:00 IST).
Purpose:  Prepare everything for the India trading day.

Flow:
  1. Check if today is a trading day (XBOM calendar)
  2. Validate existing token from .env
  3. If expired → open browser, prompt for auth code, exchange for new token
  4. Download fresh instruments master (NSE, BSE, MCX)
  5. Validate token
  6. Log success/failure + optional Telegram alert

Auth:
  Standard OAuth2 code-grant. Only needs UPSTOX_API_KEY, UPSTOX_API_SECRET,
  UPSTOX_REDIRECT_URL in .env. No MPIN, password, or TOTP secret required.
  You log in manually via browser once per day.

Exit codes:
   0 = success
   1 = auth failure
   2 = instruments download failure
   3 = token validation failure
  10 = not a trading day (expected, not an error)
  99 = unexpected error

Usage:
  python scripts/factlab_india_premarket.py
  python scripts/factlab_india_premarket.py --force       # ignore holiday check
  python scripts/factlab_india_premarket.py --auth-only   # skip instruments
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402
import exchange_calendars as xcals  # noqa: E402

from factorlab.sources.upstox.auth import ensure_token, validate_token  # noqa: E402
from factorlab.sources.upstox.instruments import refresh_all  # noqa: E402

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"factlab_india_premarket_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("factlab_india_premarket")

# ── Config ───────────────────────────────────────────────────────────────────
CALENDAR_KEY = "XBOM"
INSTRUMENTS_CACHE_DIR = PROJECT_ROOT / "data" / "upstox" / "instruments"
INSTRUMENTS_EXCHANGES = ("NSE", "BSE", "MCX")


# ── Helpers ──────────────────────────────────────────────────────────────────


def is_trading_day() -> bool:
    """Return True if today is a valid session on the XBOM calendar."""
    cal = xcals.get_calendar(CALENDAR_KEY)
    today = datetime.now().strftime("%Y-%m-%d")
    return len(cal.sessions_in_range(today, today)) > 0


def send_failure_notification(message: str) -> None:
    """Send a Telegram message on failure.  No-op if not configured."""
    import requests as _req

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (bot_token and chat_id):
        log.info("No Telegram config — skipping notification")
        return
    try:
        _req.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        log.info("Telegram notification sent")
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="India pre-market: token refresh + instruments download"
    )
    parser.add_argument("--force", action="store_true", help="Run even on non-trading days")
    parser.add_argument("--auth-only", action="store_true", help="Skip instruments download")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("factlab_india_premarket starting")
    log.info("=" * 60)

    load_dotenv(find_dotenv(usecwd=True))

    # 1. Holiday check
    if not args.force:
        if not is_trading_day():
            log.info("Not a trading day (XBOM). Exit 10.")
            return 10
        log.info("Trading day confirmed (XBOM)")
    else:
        log.info("--force: skipping holiday check")

    # 2. Authenticate (validates existing token or prompts for browser login)
    try:
        token = ensure_token()
        log.info("Auth succeeded")
    except Exception as exc:
        msg = f"Auth FAILED: {exc}"
        log.error(msg)
        send_failure_notification(f"[FactorLab] Pre-market auth failed:\n{msg}")
        return 1

    # 3. Instruments
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
    else:
        log.info("--auth-only: skipping instruments download")

    # 4. Validate token
    try:
        profile = validate_token(token)
        log.info("Token valid — user=%s", profile.get("user_name", "?"))
    except Exception as exc:
        msg = f"Token validation FAILED: {exc}"
        log.error(msg)
        send_failure_notification(f"[FactorLab] Token validation failed:\n{msg}")
        return 3

    log.info("=" * 60)
    log.info("factlab_india_premarket completed successfully")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        send_failure_notification(f"[FactorLab] Pre-market crashed:\n{exc}")
        exit_code = 99
    sys.exit(exit_code)
