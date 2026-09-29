"""EDGAR full-index parsing + per-filing URL construction.

The full-index files at /Archives/edgar/full-index/ are the canonical enumeration
of every filing. They are pipe-delimited fixed-width text (.idx) files, one per
quarter (rolling) and one per day.

    from factorlab.sources.edgar import EdgarClient, get_daily_filings
    client = EdgarClient()
    rows = get_daily_filings(client, "2026-09-20", forms={"N-1A", "497K", "4"})
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from factorlab.sources.edgar.client import EdgarClient, accession_nodash

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilingRef:
    """One row from an EDGAR index file."""
    form: str
    cik: int
    company: str
    filed_at: date
    accession: str        # dashed form: 0001193125-24-123456
    primary_doc_path: str  # e.g. edgar/data/320193/000032019324000123/aapl-20240928.htm


# ── URL builders ────────────────────────────────────────────────────────

def filing_base_url(cik: int | str, accession: str) -> str:
    """Base URL for a filing's document folder."""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash(accession)}/"


def filing_index_url(cik: int | str, accession: str) -> str:
    """JSON index describing every document in a filing."""
    return f"{filing_base_url(cik, accession)}index.json"


def form_rss_url(form: str, count: int = 40) -> str:
    """Atom feed URL for all recent filings of a given form type."""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&type={form}&count={count}&output=atom"
    )


def company_rss_url(cik: int | str, form: str = "", count: int = 40) -> str:
    """Atom feed URL for a specific filer's filings."""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={int(cik)}&type={form}&count={count}&output=atom"
    )


# ── index fetchers ──────────────────────────────────────────────────────

def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def get_quarterly_index(
    client: EdgarClient,
    year: int,
    quarter: int,
    *,
    forms: Iterable[str] | None = None,
) -> list[FilingRef]:
    """Fetch and parse the quarterly form-sorted index.

    Filter by ``forms`` (e.g. {"N-PORT-P", "13F-HR"}) to keep the payload small.
    """
    path = f"/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    resp = client.get_www(path)
    return parse_idx(resp.text, forms=forms)


def get_daily_filings(
    client: EdgarClient,
    day: date | str,
    *,
    forms: Iterable[str] | None = None,
) -> list[FilingRef]:
    """Fetch and parse the daily form-sorted index for one date.

    Returns empty list on 404 (weekends / holidays — SEC does not publish).
    """
    if isinstance(day, str):
        day = datetime.strptime(day, "%Y-%m-%d").date()
    yyyymmdd = day.strftime("%Y%m%d")
    path = (
        f"/Archives/edgar/daily-index/{day.year}/QTR{_quarter(day)}/form.{yyyymmdd}.idx"
    )
    try:
        resp = client.get_www(path)
    except Exception as exc:  # noqa: BLE001 — 404 on non-trading days is expected
        log.info("No daily index for %s: %s", day, exc)
        return []
    return parse_idx(resp.text, forms=forms)


# ── parser ──────────────────────────────────────────────────────────────

_DIVIDER_RE = re.compile(r"^-{5,}\s*$")
_WS_SPLIT_RE = re.compile(r"\s{2,}")
# form.idx / master.idx use 2+ spaces as field separators; company names can
# contain single spaces (and even single-space-then-letter runs), so 2+ works
# reliably. Date column is YYYYMMDD (no dashes).


def parse_idx(text: str, *, forms: Iterable[str] | None = None) -> list[FilingRef]:
    """Parse a .idx file body (form-sorted or CIK-sorted) into FilingRef rows.

    SEC .idx files have a preamble, a two-line wrapped header, a divider row of
    dashes, then fixed-width data rows separated by 2+ whitespace. Both
    form.idx (Form,Company,CIK,Date,File) and master.idx (CIK,Company,Form,
    Date,File) share the same rules; we detect field order per file.
    """
    wanted = {f.upper() for f in forms} if forms else None
    rows: list[FilingRef] = []

    lines = list(io.StringIO(text))
    # Locate the divider and infer field order from the preceding header block.
    divider_idx = next(
        (i for i, ln in enumerate(lines) if _DIVIDER_RE.match(ln)), -1
    )
    if divider_idx < 0:
        log.warning("Could not locate divider in .idx (%d chars)", len(text))
        return rows

    header_block = " ".join(lines[max(0, divider_idx - 3): divider_idx]).upper()
    cik_first = header_block.find("CIK") < header_block.find("FORM TYPE")

    for raw in lines[divider_idx + 1:]:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = _WS_SPLIT_RE.split(line.strip())
        if len(parts) < 5:
            continue
        # Last field is always the filename; the four fields before it are
        # (form, company, cik, date) or (cik, company, form, date).
        filename = parts[-1]
        date_str = parts[-2]
        head = parts[:-2]
        if cik_first:
            cik_s = head[0]
            company = " ".join(head[1:-1])
            form = head[-1]
        else:
            form = head[0]
            cik_s = head[-1]
            company = " ".join(head[1:-1])

        form = form.upper()
        if wanted and form not in wanted:
            continue
        try:
            cik = int(cik_s)
            filed_at = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            continue
        accession = _accession_from_filename(filename)
        if not accession:
            continue
        rows.append(
            FilingRef(
                form=form,
                cik=cik,
                company=company,
                filed_at=filed_at,
                accession=accession,
                primary_doc_path=filename,
            )
        )
    return rows


def _accession_from_filename(filename: str) -> str:
    """Extract the dashed accession from a .txt path.

    edgar/data/320193/0000320193-24-000123.txt → 0000320193-24-000123
    """
    tail = filename.rsplit("/", 1)[-1]
    stem = tail[:-4] if tail.endswith(".txt") else tail
    # Sanity: accession looks like NNNNNNNNNN-NN-NNNNNN
    if stem.count("-") == 2:
        return stem
    return ""
