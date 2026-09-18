"""House Clerk PTR PDF parser.

Extracts trade rows from a per-DocID PDF using the split-on-trailer strategy
(form-field metadata `F S:`, `S O:`, `D:` reliably appear after every trade,
regardless of how asset name / amount / type code wrap across PDF lines).

Lifted from playground/explore/house_clerk/parse_ptrs.py with CLI removed.
~83% extraction rate on the 2026 sample of 70 PTRs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

log = logging.getLogger(__name__)


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

OWNER_CODES = {"SP", "JT", "DC", "JR"}

TRAILER_RE = re.compile(
    r"\n\s*(?:F\s+S\s*:|FILING\s+STATUS\s*:)[^\n]*\n",
    re.MULTILINE | re.IGNORECASE,
)
SUB_TRAILER_RE = re.compile(
    r"^(S\s+O\s*:|D\s*:|SUBHOLDING\s+OF\s*:|FILING\s+ID\s*#"
    r"|C\s*:|L\s*:|COMMENTS\s*:|LOCATION\s*:|DESCRIPTION\s*:|EAGLE\s+SEAL)",
    re.IGNORECASE,
)
TRADE_ANCHOR_RE = re.compile(
    r"(?:^|\s)(?P<tx_type>[PSE])(?:\s*\((?:partial|p)\))?\s+"
    r"(?P<tx_date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<notif_date>\d{1,2}/\d{1,2}/\d{4})"
)
TYPE_RE = re.compile(r"\[([A-Z0-9]{2,4})\]")
AMOUNT_TOKEN_RE = re.compile(r"\$[\d,]+|Over\s*\$[\d,]+")
FORM_NOISE_RE = re.compile(r"[\x00-\x08\x0b-\x1f]")
TICKER_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9.\-]{0,9})\)")

DROP_LINE_PREFIXES = (
    "Filing ID #", "Name:", "Status:", "State/District:", "Digitally Signed:",
    "Clerk of the House", "I CERTIFY", "* For the complete list",
    "ID Owner Asset Transaction", "Type Date", "Gains", "Cap.",
    # Older-format (2014-2021) header variants — pdfplumber renders these
    # with arbitrary case due to font glyph extraction quirks; matched
    # case-insensitively below. Some have the leading capital letter on a
    # separate line, so we also list the truncated forms ("eriodic", etc.).
    "Filer Information", "Transactions", "Periodic Transaction Report",
    "Periodic Transaction", "Initial Public Offering", "Certification and Signature",
    "iD owner asset", "ID Owner",
    "eriodic ransaction", "eriodic transaction",  # leading P split to prior line
    "ransactions",  # leading T split to prior line
)
DROP_LINE_PREFIXES_LOWER = tuple(p.lower() for p in DROP_LINE_PREFIXES)

DROP_EXACT_LINES = {
    "P T R", "$200?", "Yes No", "T", "F I", "A", "C",
    "I P O", "I P O S", "Type", "Date",
}
DROP_EXACT_LINES_LOWER = {s.lower() for s in DROP_EXACT_LINES}


# ---- helpers ---------------------------------------------------------------

def parse_amount(s: str) -> tuple[int | None, int | None, int | None]:
    s_clean = re.sub(r"\s+", " ", s.replace("\n", " ")).strip().rstrip("-").strip()
    norm = s_clean.replace(" ", "")
    for k, (lo, hi) in AMOUNT_BUCKETS.items():
        if k.replace(" ", "") == norm:
            mid = ((lo + hi) // 2) if hi else lo
            return lo, hi, mid
    m = re.search(r"\$([\d,]+)\s*-\s*\$?([\d,]+)", s_clean)
    if m:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        return lo, hi, (lo + hi) // 2
    m = re.search(r"Over\s*\$([\d,]+)", s_clean)
    if m:
        lo = int(m.group(1).replace(",", ""))
        return lo, None, lo
    return None, None, None


def _clean_text(text: str) -> str:
    cleaned: list[str] = []
    for raw in text.splitlines():
        line = FORM_NOISE_RE.sub("", raw)
        s = line.strip()
        if not s:
            cleaned.append("")
            continue
        # Case-insensitive prefix + exact-line drops to handle older-format
        # PDFs where pdfplumber renders header text with arbitrary glyph case
        # (e.g. "fIler INfOrmATION" instead of "Filer Information").
        s_lower = s.lower()
        if any(s_lower.startswith(p) for p in DROP_LINE_PREFIXES_LOWER):
            continue
        if s_lower in DROP_EXACT_LINES_LOWER:
            continue
        # Drop ultra-short noise lines, but preserve [XX] type-code stubs
        # that pdfplumber sometimes wraps onto their own line.
        if len(s) <= 4 and not re.search(r"[\d$]", s):
            if not TYPE_RE.fullmatch(s):
                continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned)


def _strip_trailer_remainder(chunk: str) -> str:
    """Drop sub-trailer lines AND any comment continuations from the previous
    trade that leaked into this chunk.

    Anchor: when a chunk contains an OWNER_CODE-prefixed line (SP/JT/DC/JR),
    drop everything BEFORE it — those leading lines are previous-trade comments
    or sub-trailers like 'C:', 'L:', 'SUBHOLDING OF:'. For chunks without an
    explicit owner (self-owned trades), keep all lines and just drop sub-trailers.
    """
    lines = chunk.splitlines()
    start_idx = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        first_token = s.split()[0] if s else ""
        if first_token in OWNER_CODES:
            start_idx = i
            break

    out_lines = []
    for ln in lines[start_idx:]:
        s = ln.strip()
        if SUB_TRAILER_RE.match(s):
            continue
        out_lines.append(ln)
    return "\n".join(out_lines).strip()


def _extract_trade(chunk: str) -> dict | None:
    chunk = _strip_trailer_remainder(chunk)
    if not chunk:
        return None
    m = TRADE_ANCHOR_RE.search(chunk)
    if not m:
        return None

    pre = chunk[:m.start()]
    post = chunk[m.end():]

    pre_types = list(TYPE_RE.finditer(pre))
    post_types = list(TYPE_RE.finditer(post))
    if pre_types:
        last_type = pre_types[-1]
        asset_type = last_type.group(1)
        asset_raw = (pre[:last_type.start()] + pre[last_type.end():])
    elif post_types:
        first_type = post_types[0]
        asset_type = first_type.group(1)
        pre_type = post[:first_type.start()]
        m_amt = re.match(
            r"^\s*(?:Over\s*)?\$[\d,]+(?:\s*-(?:\s*\$?[\d,]+)?)?\s*",
            pre_type,
        )
        if m_amt:
            pre_type = pre_type[m_amt.end():]
        asset_continuation = pre_type.strip()
        asset_raw = (pre + " " + asset_continuation).strip()
    else:
        # Older 2014-2021 format: no [XX] type code in chunk.
        # Take ONLY the last non-empty line of pre as the asset row;
        # earlier lines are boilerplate header on the first chunk.
        pre_lines = [ln for ln in pre.splitlines() if ln.strip()]
        asset_raw = pre_lines[-1] if pre_lines else ""
        # STRICT POLICY: NULL beats wrong. Don't fabricate "OT". The asset_classifier
        # downstream resolves it by ticker if present; failing that, leave NULL for
        # manual / Claude review.
        asset_type = None

    owner = None
    asset_raw = asset_raw.strip()
    head, *rest = asset_raw.split(maxsplit=1)
    if head in OWNER_CODES:
        owner = head
        asset_raw = rest[0] if rest else ""

    asset_raw = "\n".join(
        ln for ln in asset_raw.splitlines()
        if ln.strip() not in DROP_EXACT_LINES
    )
    asset_raw = re.sub(r"\s+", " ", asset_raw).strip(" ,;")
    # Strip PDF form-checkbox glyph artifacts (gfedc / gfedcb) that pdfplumber
    # sometimes leaks into extracted text — they're never part of asset names.
    asset_raw = re.sub(r"\s*gfedc[b]?\b", "", asset_raw, flags=re.IGNORECASE).strip(" ,;")
    asset_raw = re.sub(r"\s+", " ", asset_raw).strip()

    # Case-insensitive ticker extraction; pdfplumber sometimes renders
    # SBUX as "SBuX" due to font glyph artifacts. Normalize to upper.
    ticker = None
    ticker_pat = re.compile(r"\(([A-Za-z][A-Za-z0-9.\-]{0,9})\)")
    matches = [
        m for m in ticker_pat.finditer(asset_raw)
        if 1 < len(m.group(1)) <= 5
        and m.group(1).upper().replace(".", "").replace("-", "").isalnum()
    ]
    if matches:
        last = matches[-1]
        ticker = last.group(1).upper()
        # Remove using the original (possibly mixed-case) captured span
        asset_raw = (asset_raw[:last.start()] + asset_raw[last.end():]).strip()
        asset_raw = re.sub(r"\s+", " ", asset_raw).strip(" ,;")

    over = re.search(r"Over\s*\$([\d,]+)", post)
    if over:
        amount_str = f"Over ${over.group(1)}"
    else:
        amounts = AMOUNT_TOKEN_RE.findall(post)[:2]
        if len(amounts) >= 2:
            amount_str = f"{amounts[0]} - {amounts[1]}"
        elif len(amounts) == 1:
            amount_str = amounts[0]
        else:
            amount_str = ""
    if not amount_str:
        return None
    lo, hi, mid = parse_amount(amount_str)

    return {
        "owner_code": owner,                     # 'SP' / 'JT' / 'DC' / 'JR' / None (=self)
        "asset_name_raw": asset_raw,
        "ticker": ticker,
        "asset_type_code": asset_type,
        "tx_type": m.group("tx_type"),           # 'P' / 'S' / 'E'
        "tx_date": m.group("tx_date"),
        "notif_date": m.group("notif_date"),
        "amount_str": amount_str,
        "amount_min": lo,
        "amount_max": hi,
        "amount_mid": mid,
    }


def parse_pdf(path: str | Path) -> dict:
    """Parse a single PTR PDF.

    Returns:
        {
          "trades": list[dict],
          "meta": {
            "kind": "paper_scan" | "electronic",
            "page_count": int,
            "text_chars_raw": int,
            "text_chars_cleaned": int,
            "anchor_matches": int,
          },
        }

    Trade dict shape:
        {owner_code, asset_name_raw, ticker, asset_type_code,
         tx_type, tx_date, notif_date, amount_str, amount_min/max/mid}

    Paper-scan heuristic: if the cleaned text is < 200 chars OR no
    TRADE_ANCHOR matches anywhere in the raw text, the PDF is a scanned
    image (or near-empty) and no trades can be extracted.
    """
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    cleaned = _clean_text(text)
    anchors = len(list(TRADE_ANCHOR_RE.finditer(text)))
    meta = {
        "page_count": n_pages,
        "text_chars_raw": len(text),
        "text_chars_cleaned": len(cleaned),
        "anchor_matches": anchors,
    }
    if len(cleaned) < 200 or anchors == 0:
        meta["kind"] = "paper_scan"
        return {"trades": [], "meta": meta}

    chunks = TRAILER_RE.split(cleaned)
    trades: list[dict] = []
    for chunk in chunks[:-1]:  # last chunk is post-final-trailer (sig page etc)
        t = _extract_trade(chunk)
        if t:
            trades.append(t)
    meta["kind"] = "electronic"
    return {"trades": trades, "meta": meta}
