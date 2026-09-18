"""Download all page-image GIFs referenced by paper-PTR JSON sidecars.

Uses the URLs in data/political/raw/senate_efd/ptrs/*_paper.json. Saves them
to data/political/raw/senate_efd/paper_images/{filing_uuid}/p{N}.gif so each
filing's pages stay grouped.

Idempotent — skips files that already exist.

Run:
    python scripts/download_paper_ptr_images.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[4]

PTR_DIR = PROJECT_ROOT / "data" / "political" / "raw" / "senate_efd" / "ptrs"
IMG_DIR = PROJECT_ROOT / "data" / "political" / "raw" / "senate_efd" / "paper_images"

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"download_paper_ptr_images_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("download_paper_ptr_images")

import os as _os
UA = {"User-Agent": _os.getenv("FACTORLAB_USER_AGENT", "FactorLab/1.0")}


def _filing_uuid_from_filename(p: Path) -> str | None:
    name = p.stem
    if not name.endswith("_paper"):
        return None
    name = name[:-len("_paper")]
    parts = name.split("_")
    if len(parts) < 7:
        return None
    return "-".join(parts[-5:])


def _filer_label_from_filename(p: Path) -> str:
    """MM-DD-YYYY_LAST_FIRST_uuid_paper.json → 'MM-DD-YYYY LAST FIRST'."""
    name = p.stem
    if name.endswith("_paper"):
        name = name[:-len("_paper")]
    parts = name.split("_")
    if len(parts) < 7:
        return p.stem
    return f"{parts[0]} {' '.join(parts[1:-5])}"


def main() -> None:
    log.info("logging to %s", log_file)
    json_files = sorted(PTR_DIR.glob("*_paper.json"))
    log.info("Found %d paper-PTR JSON sidecars (half-baked trades requiring OCR)", len(json_files))

    total_pages = 0
    total_skipped = 0
    total_downloaded = 0
    total_failed = 0
    filings_complete = 0
    filings_partial = 0

    for jf in json_files:
        uuid = _filing_uuid_from_filename(jf)
        if not uuid:
            log.warning("could not parse filename: %s", jf.name)
            continue
        sidecar = json.loads(jf.read_text(encoding="utf-8"))
        page_urls = sidecar.get("page_image_urls", [])
        out_dir = IMG_DIR / uuid
        out_dir.mkdir(parents=True, exist_ok=True)
        filer_label = _filer_label_from_filename(jf)

        per_filing_downloaded = 0
        per_filing_failed = 0
        for i, url in enumerate(page_urls, 1):
            total_pages += 1
            out_file = out_dir / f"p{i:02d}.gif"
            if out_file.exists():
                total_skipped += 1
                continue
            try:
                r = requests.get(url, headers=UA, timeout=30)
                r.raise_for_status()
                out_file.write_bytes(r.content)
                total_downloaded += 1
                per_filing_downloaded += 1
                time.sleep(0.1)
            except Exception as e:
                log.warning("fetch fail %s: %s", url, e)
                total_failed += 1
                per_filing_failed += 1

        on_disk = sum(1 for i in range(1, len(page_urls) + 1) if (out_dir / f"p{i:02d}.gif").exists())
        status = "OK" if on_disk == len(page_urls) and len(page_urls) > 0 else "PARTIAL"
        if status == "OK":
            filings_complete += 1
        else:
            filings_partial += 1
        log.info("[GIF] %-7s %s | uuid=%s pages=%d/%d (downloaded=%d failed=%d)",
                 status, filer_label, uuid, on_disk, len(page_urls),
                 per_filing_downloaded, per_filing_failed)

    log.info("Done. filings: %d complete, %d partial. pages: total=%d downloaded=%d skipped=%d failed=%d",
             filings_complete, filings_partial,
             total_pages, total_downloaded, total_skipped, total_failed)


if __name__ == "__main__":
    main()
