#!/usr/bin/env python3
"""FactorLab Schwab Auth -- OAuth bootstrap + weekly re-auth driver.

Mints (or refreshes) the Schwab API token via browser OAuth, verifies the
connection by hitting AAPL quote. Run this:

  - First time, after the dev-portal app reaches "Ready For Use"
  - Weekly, before the 7-day refresh token expires (Sun/Mon morning works well)

Usage:
    python scripts/us/equities/schwab/us_equities_schwab_auth.py
    python scripts/us/equities/schwab/us_equities_schwab_auth.py --force        # delete token first
    python scripts/us/equities/schwab/us_equities_schwab_auth.py --no-verify    # skip the AAPL test call
"""

import argparse
import logging
import sys
from pathlib import Path

# Path layout: scripts/us/equities/schwab/<file>.py
#   parents[0]=schwab, [1]=equities, [2]=us, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.countries.us.equities.schwab import (
    delete_token,
    ensure_client,
    token_file_exists,
    token_file_path,
    validate_client,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("us_equities_schwab_auth")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Schwab OAuth bootstrap / weekly re-auth")
    p.add_argument("--force", action="store_true",
                   help="Delete existing token first, force browser OAuth")
    p.add_argument("--no-verify", action="store_true",
                   help="Skip the AAPL quote verification call")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.force:
        delete_token()

    log.info("Token file exists: %s", token_file_exists())

    client = ensure_client(interactive=True)

    if args.no_verify:
        log.info("Skipping verification (--no-verify)")
        print("\nSchwab token at:", token_file_path())
        return

    log.info("Verifying with quote('AAPL') ...")
    payload = validate_client(client)
    quote = payload.get("AAPL", {}).get("quote", {})
    last = quote.get("lastPrice") or quote.get("closePrice")
    log.info("OK AAPL last price: %s", last)

    print("\nSchwab auth ready.")
    print("Token at:", token_file_path())
    print("Refresh token valid 7 days -- re-run this script weekly.")


if __name__ == "__main__":
    main()
