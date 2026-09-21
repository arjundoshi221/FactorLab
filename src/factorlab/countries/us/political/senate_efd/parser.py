"""Senate eFD HTML PTR parser.

Modern e-filed PTRs come back as a single HTML <table> with columns:
  # | Transaction Date | Owner | Ticker | Asset Name | Asset Type | Type | Amount | Comment

100% extraction rate on e-filed (the 90% case). Paper-filed PTRs (GIF scans)
need OCR — those get archived only.

Lifted from playground/explore/senate_efd/parse_html.py with CLI removed.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

AMOUNT_BUCKETS = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, None),
}


def parse_amount(s: str) -> tuple[int | None, int | None, int | None]:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if s in AMOUNT_BUCKETS:
        lo, hi = AMOUNT_BUCKETS[s]
        return lo, hi, ((lo + hi) // 2) if hi else lo
    m = re.search(r"\$([\d,]+)\s*-\s*\$?([\d,]+)", s)
    if m:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        return lo, hi, (lo + hi) // 2
    m = re.search(r"Over\s*\$([\d,]+)", s, re.I)
    if m:
        lo = int(m.group(1).replace(",", ""))
        return lo, None, lo
    return None, None, None


def normalize_owner(o: str) -> str:
    o = (o or "").strip()
    return {
        "Self": "self", "self": "self",
        "Spouse": "spouse",
        "Joint": "joint",
        "Child": "dependent_child",
        "Dependent Child": "dependent_child",
        "--": "self",
    }.get(o, "self")


def normalize_tx_type(t: str) -> str | None:
    """Map raw `Type` cell text to a canonical tx_type.

    Strict-NULL: returns None on unknown so callers leave the column NULL
    rather than fabricate 'purchase'.
    """
    t = (t or "").strip().lower()
    if "sale (full)" in t:
        return "sale_full"
    if "sale (partial)" in t:
        return "sale_partial"
    if "purchase" in t:
        return "purchase"
    if "exchange" in t:
        return "exchange"
    return None


def parse_html(path: str | Path) -> list[dict]:
    """Parse one /search/view/ptr/ HTML page → list of trade-row dicts."""
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []
    header = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]

    def col(name: str) -> int:
        # Exact-match first to avoid `"type" in "asset type"` false-positives
        # that would route the Type column to the Asset Type column. Only fall
        # back to substring match if no header matches exactly.
        for i, h in enumerate(header):
            if h == name:
                return i
        for i, h in enumerate(header):
            if name in h:
                return i
        return -1

    idx = {
        "txn_date": col("transaction date"),
        "owner": col("owner"),
        "ticker": col("ticker"),
        "asset_name": col("asset name"),
        "asset_type": col("asset type"),
        "type": col("type"),
        "amount": col("amount"),
        "comment": col("comment"),
    }
    out: list[dict] = []
    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells or len(cells) < max(idx.values()) + 1:
            continue
        amt = cells[idx["amount"]] if idx["amount"] >= 0 else ""
        lo, hi, mid = parse_amount(amt)
        ticker = (cells[idx["ticker"]] if idx["ticker"] >= 0 else "").strip()
        if ticker in {"--", "", "N/A"}:
            ticker = None
        # Senate uses words for asset_type ('Stock', 'Corporate Bond', etc.) — we
        # keep them in asset_name_raw and don't try to map to House's 2-char codes
        out.append({
            "owner": normalize_owner(cells[idx["owner"]] if idx["owner"] >= 0 else ""),
            "asset_name_raw": cells[idx["asset_name"]] if idx["asset_name"] >= 0 else "",
            "ticker": ticker,
            "asset_type_word": cells[idx["asset_type"]] if idx["asset_type"] >= 0 else "",
            "tx_type": normalize_tx_type(cells[idx["type"]] if idx["type"] >= 0 else ""),
            "tx_date_str": cells[idx["txn_date"]] if idx["txn_date"] >= 0 else "",
            "amount_str": amt,
            "amount_min": lo,
            "amount_max": hi,
            "amount_mid": mid,
            "comment": cells[idx["comment"]] if idx["comment"] >= 0 else "",
        })
    return out
