"""House Clerk year-index loader.

The annual `{YEAR}FD.ZIP` is filing metadata only (no transaction data) —
parses to a list of (DocID, filing_date, filer name, state_dst). Filter to
FilingType='P' for PTRs.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# SECURITY: stdlib xml.etree is vulnerable to billion-laughs / quadratic-blowup
# DoS via crafted internal entities. House Clerk is trusted but a MITM could
# poison the year-XML index in transit. defusedxml is a drop-in API replacement.
import defusedxml.ElementTree as ET

from factorlab.countries.us.political._client import PoliticalHTTPClient

log = logging.getLogger(__name__)

# Decompressed XML inside the year-ZIP must not exceed this size — guards
# against zipbomb (4-byte ZIP decompressing to 4 GB and OOMing the process).
MAX_XML_DECOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB

ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"


def fetch_year_index(client: PoliticalHTTPClient, year: int | str) -> ET.ElementTree:
    """Download {year}FD.ZIP, extract its XML, return parsed tree.

    Caches both the ZIP and the extracted XML under data/political/raw/house_clerk/indexes/.
    The current calendar year is **always re-fetched** via ``max_age_sec=0`` —
    House Clerk publishes new PTRs daily, and a stale cache would silently
    miss them. Prior years are treated as immutable archives.
    """
    from factorlab.countries.us.political._client import DATA_ROOT

    year = str(year)
    indexes_dir = DATA_ROOT / "house_clerk" / "indexes"
    xml_disk = indexes_dir / f"{year}FD.xml"
    current_year = str(datetime.now(timezone.utc).year)
    is_current = (year == current_year)
    if xml_disk.exists() and not is_current:
        return ET.parse(xml_disk)

    # Fetch ZIP via the client so raw_archive is populated.
    # Current year: force-refetch (max_age_sec=0). Prior year (rare path —
    # missing XML on disk): use the legacy forever-cache.
    body, _ = client.get(
        ZIP_URL.format(year=year),
        save_as=Path("indexes") / f"{year}FD.ZIP",
        ext="zip",
        max_age_sec=0 if is_current else None,
    )
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        xml_name = next((n for n in z.namelist() if n.lower().endswith(".xml")), None)
        if not xml_name:
            raise RuntimeError(f"no XML inside {year}FD.ZIP")
        # SECURITY: reject `..` or absolute-path entry names (Zip Slip)
        if ".." in xml_name or xml_name.startswith(("/", "\\")):
            raise RuntimeError(
                f"unsafe entry name in {year}FD.ZIP: {xml_name!r}"
            )
        # Cap decompressed size (zipbomb defense)
        info = z.getinfo(xml_name)
        if info.file_size > MAX_XML_DECOMPRESSED_BYTES:
            raise RuntimeError(
                f"{year}FD.ZIP entry {xml_name!r} would decompress to "
                f"{info.file_size:,} bytes (cap: {MAX_XML_DECOMPRESSED_BYTES:,})"
            )
        xml_bytes = z.read(xml_name)
    xml_disk.parent.mkdir(parents=True, exist_ok=True)
    xml_disk.write_bytes(xml_bytes)
    # defusedxml exposes parse() but not the ElementTree constructor — re-parse
    # from disk to get a proper tree.
    return ET.parse(xml_disk)


def list_ptrs(
    client: PoliticalHTTPClient,
    years: list[int],
    *,
    limit_per_year: int | None = None,
) -> list[dict]:
    """Return PTR filings (FilingType='P') across the given years.

    Each row has: doc_id, year, filing_date (M/D/YYYY string), first, last, state_dst.
    Sorted newest filing first.
    """
    out: list[dict] = []
    for year in years:
        try:
            tree = fetch_year_index(client, year)
        except Exception as e:
            log.warning("[house_clerk] year %s index fetch fail: %s", year, e)
            continue
        year_count = 0
        for m in tree.getroot():
            if (m.findtext("FilingType") or "").strip() != "P":
                continue
            doc_id = (m.findtext("DocID") or "").strip()
            if not doc_id:
                continue
            out.append({
                "doc_id": doc_id,
                "year": (m.findtext("Year") or str(year)).strip(),
                "filing_date": (m.findtext("FilingDate") or "").strip(),
                "first": (m.findtext("First") or "").strip(),
                "last": (m.findtext("Last") or "").strip(),
                "state_dst": (m.findtext("StateDst") or "").strip(),
            })
            year_count += 1
            if limit_per_year and year_count >= limit_per_year:
                break
        log.info("[house_clerk] year %s: %d PTRs in index", year, year_count)

    # Sort newest filing first
    def _key(r: dict) -> tuple:
        try:
            mo, da, yr = r["filing_date"].split("/")
            return (int(yr), int(mo), int(da))
        except Exception:
            return (0, 0, 0)
    out.sort(key=_key, reverse=True)
    return out
