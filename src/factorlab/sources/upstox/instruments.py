"""Upstox instruments master — download, cache, search.

Published daily at ~6 AM IST by Upstox CDN.  No auth needed to download.
Cache directory: data/upstox/instruments/ (from india.yaml instruments_master.cache_dir)
"""

import gzip
import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_URL_TEMPLATE = (
    "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
)
_DEFAULT_EXCHANGES = ("NSE", "BSE", "MCX")


def download_instruments(exchange: str, cache_dir: Path) -> list[dict]:
    """Download instruments master for one exchange, decompress, and cache as JSON."""
    url = _URL_TEMPLATE.format(exchange=exchange)
    log.info("Downloading %s instruments from %s", exchange, url)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(gzip.decompress(resp.read()))
    log.info("Downloaded %s: %d instruments", exchange, len(data))

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"instruments_{exchange.lower()}.json"
    with open(cache_path, "w") as f:
        json.dump(data, f)
    log.info("Cached → %s", cache_path)
    return data


def load_or_download(exchange: str, cache_dir: Path) -> list[dict]:
    """Return cached instruments if fresh (same calendar date), otherwise download."""
    cache_path = cache_dir / f"instruments_{exchange.lower()}.json"
    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if mtime.date() == datetime.now().date():
            with open(cache_path) as f:
                data = json.load(f)
            log.info(
                "Using cached %s instruments (%d items, mtime=%s)",
                exchange, len(data), mtime.date(),
            )
            return data
    return download_instruments(exchange, cache_dir)


def refresh_all(
    cache_dir: Path,
    exchanges: tuple[str, ...] = _DEFAULT_EXCHANGES,
) -> dict[str, list[dict]]:
    """Download fresh instruments for all configured exchanges.

    Returns ``{exchange: [instrument_records]}``.
    """
    result: dict[str, list[dict]] = {}
    for exch in exchanges:
        result[exch] = download_instruments(exch, cache_dir)
    return result


def find_equities(
    instruments: list[dict],
    segment: str = "NSE_EQ",
) -> dict[str, dict]:
    """Build ``{trading_symbol: instrument_record}`` lookup for EQ instruments."""
    return {
        i["trading_symbol"]: i
        for i in instruments
        if i.get("segment") == segment and i.get("instrument_type") == "EQ"
    }


def find_nearest_future(
    instruments: list[dict],
    underlying: str,
    segment: str = "NSE_FO",
) -> dict | None:
    """Return the nearest-expiry FUT contract for *underlying*, or ``None``."""
    candidates = [
        i for i in instruments
        if i.get("segment") == segment
        and i.get("instrument_type") == "FUT"
        and i.get("underlying_symbol") == underlying
    ]
    candidates.sort(key=lambda x: x.get("expiry", 0))
    return candidates[0] if candidates else None
