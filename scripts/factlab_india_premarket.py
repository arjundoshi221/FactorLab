"""factlab_india_premarket — Daily token sync + instruments + validation.

Schedule: Run before 09:15 IST on trading days (e.g. 08:00-08:30 IST).
          First login on Railway, then run this script locally.

Flow:
  1. Check if today is a trading day (XBOM calendar)
  2. Sync token from Railway auth server (or validate local token)
  3. Download fresh instruments master (NSE, BSE, MCX)
  4. Final token validation
  5. Email + Telegram alert on failure (silent until configured)

Auth resolution order:
  1. Local .token file / .env → validate
  2. Railway auth server → validate + save locally
  3. Interactive browser login (last resort)

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
from factorlab.sources.upstox.universes import build_universes  # noqa: E402

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
INSTRUMENTS_EXCHANGES = ("NSE",)
UNIVERSES_DIR = PROJECT_ROOT / "data" / "in" / "universes"


# ── Helpers ──────────────────────────────────────────────────────────────────


def is_trading_day() -> bool:
    """Return True if today is a valid session on the XBOM calendar."""
    cal = xcals.get_calendar(CALENDAR_KEY)
    today = datetime.now().strftime("%Y-%m-%d")
    return len(cal.sessions_in_range(today, today)) > 0


def send_failure_notification(message: str) -> None:
    """Send failure alert via email and/or Telegram. No-op if not configured."""
    _send_email(message)
    _send_telegram(message)


def _send_email(message: str) -> None:
    """Send email alert. Requires NOTIFY_EMAIL + SMTP_* env vars."""
    import smtplib
    from email.mime.text import MIMEText

    to_addr = os.environ.get("NOTIFY_EMAIL", "").strip()
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASSWORD", "").strip()
    if not (to_addr and smtp_user and smtp_pass):
        log.info("No email config — skipping email notification")
        return

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587").strip())

    msg = MIMEText(message)
    msg["Subject"] = "[FactorLab] Pre-market setup FAILED"
    msg["From"] = smtp_user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        log.info("Email notification sent to %s", to_addr)
    except Exception as exc:
        log.warning("Email notification failed: %s", exc)


def _send_telegram(message: str) -> None:
    """Send Telegram alert. Requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."""
    import requests as _req

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (bot_token and chat_id):
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
        description="India pre-market: token sync + instruments + validation"
    )
    parser.add_argument("--force", action="store_true", help="Run even on non-trading days")
    parser.add_argument("--auth-only", action="store_true", help="Skip instruments download")
    parser.add_argument("--headless", action="store_true", help="No interactive login (Railway mode)")
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

    # 2. Authenticate
    #    ensure_token() tries: local .token → .env → Railway auth server → browser login
    try:
        token = ensure_token(interactive=not args.headless)
        log.info("Auth succeeded")
    except Exception as exc:
        msg = f"Auth FAILED: {exc}"
        log.error(msg)
        send_failure_notification(f"[FactorLab] Pre-market auth failed:\n{msg}")
        return 1

    # 3. Validate token before doing any API work
    try:
        profile = validate_token(token)
        log.info("Token valid — user=%s", profile.get("user_name", "?"))
    except Exception as exc:
        msg = f"Token validation FAILED: {exc}"
        log.error(msg)
        send_failure_notification(f"[FactorLab] Token validation failed:\n{msg}")
        return 3

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
