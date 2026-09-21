"""Shared universe loader + builder for India equities.

Universe files live at ``data/in/universes/{name}_latest.yaml`` in YAML format:

    generated: "2026-04-26"
    count: 213
    symbols:
      - 360ONE
      - ABB
      - ...

Build pipeline (called from premarket):
  1. ``build_fo_eligible()`` — derive F&O eligible from instruments master
  2. ``fetch_nse_index_constituents()`` — pull current Nifty 50/100/500/etc. lists
  3. ``build_universes()`` — write all YAMLs append-only (symbols never removed)

Load pipeline (called from 5min / hourly):
  1. ``load_universe()`` — read india.yaml, resolve inline symbols or source_file
"""

import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

log = logging.getLogger(__name__)

# Index names that appear as FUT underlyings but are not single stocks
_INDEX_UNDERLYINGS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "SENSEX", "BANKEX",
}


def load_universe(name: str, project_root: Path) -> list[str]:
    """Load symbol list from ``configs/universes/india.yaml``.

    Supports:
      - Inline ``symbols: [...]`` in config
      - ``source_file:`` pointing to a ``.yaml`` or ``.csv`` file
    """
    cfg_path = project_root / "configs" / "universes" / "india.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    universe = cfg.get("universes", {}).get(name)
    if not universe:
        raise ValueError(f"Universe '{name}' not found in {cfg_path}")

    # Inline symbols
    if "symbols" in universe:
        return universe["symbols"]

    # File-based universe
    src = universe.get("source_file")
    if src:
        src_path = project_root / src
        if not src_path.exists():
            raise FileNotFoundError(
                f"Universe file {src_path} does not exist. "
                f"Run premarket to generate it, or provide a seed file."
            )
        return _read_universe_file(src_path)

    raise ValueError(f"Universe '{name}' has no symbols or source_file")


def _read_universe_file(path: Path) -> list[str]:
    """Read symbols from a .yaml or .csv universe file."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        symbols = data.get("symbols", [])
        if not symbols:
            raise ValueError(f"No 'symbols' key in {path}")
        return symbols
    elif suffix == ".csv":
        import pandas as pd
        df = pd.read_csv(path)
        col = "symbol" if "symbol" in df.columns else df.columns[0]
        return df[col].tolist()
    else:
        raise ValueError(f"Unsupported universe file format: {suffix}")


def build_fo_eligible(instruments: list[dict]) -> list[str]:
    """Derive F&O eligible single-stock symbols from instruments master.

    Finds all unique underlying_symbol values from NSE_FO FUT contracts,
    then removes index names. Only keeps symbols that also exist in NSE_EQ.
    Returns sorted, deduplicated list.
    """
    # EQ = rolling settlement; BE = Book Entry / surveillance for new listings.
    # Both are tradeable equities — match the same definition used by
    # find_equities() and sync_instruments().
    equities = {
        i["trading_symbol"]
        for i in instruments
        if i.get("segment") == "NSE_EQ" and i.get("instrument_type") in ("EQ", "BE")
    }
    fo_underlyings = {
        i["underlying_symbol"]
        for i in instruments
        if i.get("segment") == "NSE_FO"
        and i.get("instrument_type") == "FUT"
        and i.get("underlying_symbol")
    }
    # Keep only single stocks (not indices) that exist in NSE_EQ
    stocks = (fo_underlyings - _INDEX_UNDERLYINGS) & equities
    return sorted(stocks)


_NSE_INDEX_URLS = {
    "nifty50":      "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "nifty_next50": "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv",
    "nifty100":     "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty500":     "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "midcap150":    "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "smallcap250":  "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "microcap250":  "https://archives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
}

# NSE blocks bare python-requests; a browser UA is required.
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def fetch_nse_index_constituents(name: str, *, timeout: int = 20) -> list[str]:
    """Fetch current constituents of an NSE index from the public archive.

    Returns sorted list of trading symbols. Raises on HTTP error or missing
    Symbol column.
    """
    url = _NSE_INDEX_URLS[name]
    r = requests.get(url, headers=_NSE_HEADERS, timeout=timeout)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    sym_col = next((c for c in (reader.fieldnames or []) if c.strip().lower() == "symbol"), None)
    if not sym_col:
        raise ValueError(f"NSE CSV for {name} missing 'Symbol' column: cols={reader.fieldnames}")
    syms = {row[sym_col].strip() for row in reader if row.get(sym_col)}
    return sorted(syms)


def build_universes(
    instruments: list[dict],
    output_dir: Path,
) -> dict[str, int]:
    """Build universe YAML files from instruments master + NSE index archives.

    Append-only: existing symbols are preserved, new symbols are added.
    NSE fetch failures are logged and skipped so premarket still succeeds
    even if NSE archive is temporarily unreachable.
    Returns ``{universe_name: symbol_count}``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, int] = {}

    # F&O eligible — auto-derived from instruments master
    fo_symbols = build_fo_eligible(instruments)
    fo_path = output_dir / "fo_eligible_latest.yaml"
    merged = _append_only_merge(fo_path, fo_symbols)
    _write_universe_yaml(fo_path, merged)
    result["fo_eligible"] = len(merged)
    log.info("Built fo_eligible: %d symbols (append-only)", len(merged))

    # NSE index universes — pulled from public archive CSVs
    for name in _NSE_INDEX_URLS:
        try:
            symbols = fetch_nse_index_constituents(name)
        except Exception as exc:
            log.warning("NSE fetch failed for %s: %s — keeping existing yaml", name, exc)
            continue
        path = output_dir / f"{name}_latest.yaml"
        prior = len(_read_existing_yaml(path))
        merged = _append_only_merge(path, symbols)
        _write_universe_yaml(path, merged)
        result[name] = len(merged)
        log.info("Built %s: %d symbols (append-only, +%d new)",
                 name, len(merged), len(merged) - prior)

    return result


def seed_index_universe(
    name: str,
    symbols: list[str],
    output_dir: Path,
) -> bool:
    """Write initial YAML if file doesn't exist. Returns True if seeded."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}_latest.yaml"
    if path.exists():
        log.info("Seed skip: %s already exists (%d symbols)", name, len(_read_existing_yaml(path)))
        return False
    _write_universe_yaml(path, sorted(symbols))
    log.info("Seeded %s: %d symbols", name, len(symbols))
    return True


def _append_only_merge(path: Path, new_symbols: list[str]) -> list[str]:
    """Merge new symbols with existing file (union, sorted). Never removes."""
    existing = set(_read_existing_yaml(path))
    merged = existing | set(new_symbols)
    return sorted(merged)


def _read_existing_yaml(path: Path) -> list[str]:
    """Read symbols from existing YAML file. Returns [] if not found."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("symbols", []) if data else []
    except Exception:
        return []


def _write_universe_yaml(path: Path, symbols: list[str]) -> None:
    """Write universe YAML with metadata header."""
    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "count": len(symbols),
        "symbols": symbols,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    log.info("Wrote %s (%d symbols)", path.name, len(symbols))
