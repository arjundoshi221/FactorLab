#!/usr/bin/env python3
"""us_equities_blackrock_universe -- build US universe yamls from public sources.

Russell 3000: pulls iShares IWV ETF holdings CSV from BlackRock (free, public,
refreshed daily). The IWV ETF tracks the Russell 3000 closely (~3,000 holdings).

Run quarterly to capture index reconstitution + drift.

Usage:
    python scripts/us/equities/blackrock/us_equities_blackrock_universe.py --target russell3000
    python scripts/us/equities/blackrock/us_equities_blackrock_universe.py --target russell3000 --top 500   # writes us_sp500.yaml
"""

import argparse
import csv
import io
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# Path layout: scripts/us/equities/blackrock/<file>.py
#   parents[0]=blackrock, [1]=equities, [2]=us, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
UNIVERSES_DIR = PROJECT_ROOT / "configs" / "universes"
UNIVERSES_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("us_equities_blackrock_universe")

# BlackRock public IWV ETF holdings CSV
IWV_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IWV_holdings&dataType=fund"
)


def fetch_iwv_holdings() -> list[dict]:
    """Download iShares IWV holdings CSV. Returns list of holding dicts."""
    log.info("Fetching IWV holdings from BlackRock ...")
    r = requests.get(IWV_HOLDINGS_URL, timeout=60)
    r.raise_for_status()
    text = r.text

    # BlackRock CSV has a multi-line header; the holdings table starts after
    # a row that begins with `Ticker,Name,...`
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Ticker,") or line.startswith('"Ticker"'):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("Couldn't find holdings header row in IWV CSV. "
                           "BlackRock may have changed the format.")

    holdings_csv = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(holdings_csv))
    holdings = []
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        if not ticker or ticker == "-":
            continue
        # Filter to actual equity holdings (skip cash, FX, etc.)
        asset_class = (row.get("Asset Class") or "").strip()
        if asset_class and asset_class.lower() not in ("equity", "stock"):
            continue
        # Parse weight for sorting
        weight_str = (row.get("Weight (%)") or row.get("Weight") or "0").replace(",", "")
        try:
            weight = float(weight_str)
        except ValueError:
            weight = 0.0
        holdings.append({
            "ticker": ticker,
            "name": (row.get("Name") or "").strip(),
            "weight": weight,
            "exchange": (row.get("Exchange") or "").strip(),
        })

    log.info("Parsed %d equity holdings", len(holdings))
    return holdings


def normalize_ticker(ticker: str) -> str:
    """Normalize BlackRock ticker to FactorLab canonical form.

    BlackRock typically uses the same dot-form we use (BRK.B). Just strip
    whitespace and uppercase. (Schwab translation -- BRK.B -> BRK/B -- is
    handled at the API boundary by ``factorlab.countries.us.equities.schwab.symbols``.)
    """
    return ticker.strip().upper()


def write_universe_yaml(name: str, symbols: list[str], description: str) -> Path:
    """Write a universe yaml file."""
    path = UNIVERSES_DIR / f"us_{name}.yaml"
    payload = {
        "name": name,
        "country": "US",
        "description": description,
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "source": "iShares IWV ETF holdings (https://www.ishares.com/us/products/239714/)",
        "count": len(symbols),
        "symbols": symbols,
    }
    with open(path, "w") as f:
        yaml.safe_dump(payload, f, sort_keys=False)
    log.info("Wrote %s (%d symbols)", path, len(symbols))
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build US universe yamls from IWV holdings")
    p.add_argument("--target", default="russell3000",
                   help="Universe name to write (default: russell3000)")
    p.add_argument("--top", type=int,
                   help="Take only top-N by weight (writes us_<name>.yaml with N symbols). "
                        "E.g. --target sp500 --top 500 -> us_sp500.yaml with 500 largest names.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    holdings = fetch_iwv_holdings()
    if not holdings:
        log.error("No holdings parsed -- aborting")
        return 1

    # Sort by weight descending (proxy for market cap in a market-cap-weighted ETF)
    holdings.sort(key=lambda h: h["weight"], reverse=True)

    if args.top:
        holdings = holdings[:args.top]
        log.info("Filtered to top %d by weight", args.top)

    symbols = [normalize_ticker(h["ticker"]) for h in holdings]
    # Dedup (preserves order)
    seen = set()
    deduped = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    desc = "Russell 3000 constituents from iShares IWV ETF holdings"
    if args.top:
        desc = f"Top {args.top} US equities from IWV (proxy for S&P {args.top})"

    write_universe_yaml(args.target, deduped, desc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
