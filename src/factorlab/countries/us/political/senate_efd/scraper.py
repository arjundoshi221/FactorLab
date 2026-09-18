"""Senate eFD scraper — Playwright-driven, runs on your local Windows machine.

Akamai blocks all programmatic GETs from datacenter IPs. From a residential
IP + a real Chromium, the site behaves normally. Flow:
  1. /search/home/  → accept T&C
  2. /search/       → set filters (Senator, Periodic Transactions, date range)
  3. /search/report → result rows
  4. Per row:       /search/view/ptr/{uuid}/  (HTML, modern e-filed)
                    /search/view/paper/{uuid}/ (HTML viewer + GIF page set)

Lifted from playground/explore/senate_efd/scrape.py with:
  - headless=True default (production)
  - screenshots disabled
  - returns dict-of-lists for ingest layer
  - pdfs go under data/political/raw/senate_efd/
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import requests
from playwright.sync_api import Page, sync_playwright

from factorlab.countries.us.political._client import DATA_ROOT

log = logging.getLogger(__name__)

BASE = "https://efdsearch.senate.gov"
SAVE_DIR = DATA_ROOT / "senate_efd"
PTR_DIR = SAVE_DIR / "ptrs"
PAPER_GIFS_DIR = SAVE_DIR / "papers"  # one subfolder per paper-filing slug
SCREENSHOTS = SAVE_DIR / "_screenshots"

# Public CDN for paper-filing page images. No Akamai protection — plain
# HTTP GETs work fine from any IP, so we don't need Playwright for the
# pixel data. Only the PTR landing pages live behind the bot wall.
PAPER_GIF_TIMEOUT = 30
PAPER_GIF_MAX_RETRIES = 2


@dataclass
class ScrapeResult:
    search_results: list[dict] = field(default_factory=list)
    downloaded: list[dict] = field(default_factory=list)


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:50]


def _accept_agreement(page: Page) -> None:
    page.goto(f"{BASE}/search/home/", wait_until="domcontentloaded")
    page.locator("input[type='checkbox']").first.check()
    page.locator("button[type='submit'], input[type='submit']").first.click()
    page.wait_for_load_state("networkidle")


def _run_search(page: Page, *, date_from: str, date_to: str, filer: str = "Senator") -> None:
    page.goto(f"{BASE}/search/", wait_until="domcontentloaded")
    page.get_by_label(filer, exact=True).check()
    for label in ("Annual", "Due Date Extension", "Blind Trusts", "Other Documents"):
        try:
            page.get_by_label(label, exact=True).uncheck(timeout=1000)
        except Exception:
            pass
    page.get_by_label("Periodic Transactions", exact=True).check()
    page.locator("input[name='submitted_start_date']").fill(date_from)
    page.locator("input[name='submitted_end_date']").fill(date_to)
    page.get_by_role("button", name=re.compile("Search.*Reports", re.I)).click()
    page.wait_for_selector("table tbody tr, .alert-warning", timeout=30000)
    page.wait_for_load_state("networkidle")


def _collect_results(page: Page) -> list[dict]:
    out: list[dict] = []
    page_num = 1
    while True:
        page.wait_for_selector("table tbody tr", timeout=15000)
        rows = page.locator("table tbody tr").all()
        for row in rows:
            cells = [c.strip() for c in row.locator("td").all_text_contents()]
            if len(cells) < 5:
                continue
            # Scope link extraction to filing-view URLs only — prevents picking up
            # tooltip/help/sort anchors that might be embedded in a row.
            # NO fallback to row.locator("a").first — that can pair a row's name
            # cells with a different row's link (the Hagerty/Blumenthal bug).
            view_links = row.locator("a[href*='/search/view/']")
            try:
                href = view_links.first.get_attribute("href") if view_links.count() else ""
            except Exception:
                href = ""
            review_reasons: list[str] = []
            if not href:
                review_reasons.append("no_view_link")
            if cells[0] and not any(ch.isalpha() for ch in cells[0]):
                review_reasons.append(f"non_name_cells0={cells[0]!r}")

            row_dict = {
                "first": cells[0],
                "last": cells[1],
                "office": cells[2],
                "report_type": cells[3],
                "filed_date": cells[4],
                "link": urljoin(BASE, href) if href else None,
                "needs_review": bool(review_reasons),
                "review_reasons": review_reasons,
            }
            out.append(row_dict)
            if review_reasons:
                log.warning("[senate_efd] row flagged for review: %s  cells=%s  link=%s",
                            ",".join(review_reasons), cells[:5], href or "(none)")
                # Persist to JSONL audit file so the user can manually inspect later
                review_file = SAVE_DIR / "_review" / "search_rows.jsonl"
                review_file.parent.mkdir(parents=True, exist_ok=True)
                with review_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row_dict) + "\n")
        nxt = page.locator("a.paginate_button.next:not(.disabled), .paginate_button.next:not(.disabled) a")
        if nxt.count() == 0:
            break
        cls = nxt.first.get_attribute("class") or ""
        if "disabled" in cls:
            break
        nxt.first.click()
        page.wait_for_load_state("networkidle")
        time.sleep(0.4)
        page_num += 1
    log.info("[senate_efd] collected %d result rows across %d pages", len(out), page_num)
    return out


def backfill_paper_gifs_from_sidecars() -> tuple[int, int]:
    """Scan all `*_paper.json` sidecars in PTR_DIR; for any whose GIFs aren't
    already on disk, download them. Idempotent.

    Used at the start of `ingest_senate_efd` so paper filings cached by
    earlier runs (which only saved viewer HTML + URL list) get their pixel
    data populated without a re-scrape.

    Returns: (filings_processed, gifs_downloaded).
    """
    n_filings = 0
    n_dl = 0
    if not PTR_DIR.exists():
        return 0, 0
    for sidecar in PTR_DIR.glob("*_paper.json"):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("[senate_efd] sidecar unreadable %s: %s", sidecar.name, e)
            continue
        urls = meta.get("page_image_urls") or []
        if not urls:
            continue
        # Slug = sidecar filename without `_paper.json` suffix
        slug = sidecar.stem.removesuffix("_paper")
        before = len(list((PAPER_GIFS_DIR / slug).glob("*"))) if (PAPER_GIFS_DIR / slug).exists() else 0
        local = _download_paper_gifs(slug, urls)
        after = len(list((PAPER_GIFS_DIR / slug).glob("*")))
        n_filings += 1
        n_dl += max(0, after - before)
        # Also patch the sidecar with the local paths if missing
        if "local_page_paths" not in meta or len(meta.get("local_page_paths") or []) != len(local):
            meta["local_page_paths"] = local
            meta["downloaded_pages"] = len(local)
            sidecar.write_text(json.dumps(meta, indent=2))
    return n_filings, n_dl


def _download_paper_gifs(slug: str, urls: list[str]) -> list[str]:
    """Download the paper-PTR page images to local disk.

    Idempotent: skips any GIF already on disk (zero-byte files are treated
    as missing and re-downloaded). Returns the list of local relative paths
    that exist after the call.

    Errors on individual GIFs are logged but don't fail the filing — we'd
    rather have an incomplete OCR set than no record of the paper PTR at
    all. The viewer HTML + sidecar JSON still get written either way.
    """
    if not urls:
        return []
    folder = PAPER_GIFS_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)
    local_paths: list[str] = []
    for i, url in enumerate(urls, 1):
        # Preserve the source extension — practically always .gif here, but
        # guard against the CDN ever serving .png/.tif.
        ext = Path(url).suffix.lower() or ".gif"
        local = folder / f"page_{i:03d}{ext}"
        if local.exists() and local.stat().st_size > 0:
            local_paths.append(str(local.relative_to(DATA_ROOT.parent)))
            continue
        for attempt in range(PAPER_GIF_MAX_RETRIES + 1):
            try:
                resp = requests.get(url, timeout=PAPER_GIF_TIMEOUT)
                resp.raise_for_status()
                local.write_bytes(resp.content)
                local_paths.append(str(local.relative_to(DATA_ROOT.parent)))
                break
            except requests.RequestException as e:
                if attempt >= PAPER_GIF_MAX_RETRIES:
                    log.warning("[senate_efd] paper GIF download failed %s: %s",
                                url, e)
                else:
                    time.sleep(0.5 * (attempt + 1))
    return local_paths


def _download_filing(page: Page, r: dict) -> dict | None:
    """Navigate the same tab to the result link; save HTML or PDF; return manifest dict."""
    url = r.get("link")
    if not url:
        return None
    try:
        response = page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        log.warning("[senate_efd] nav fail %s: %s", url, e)
        return None
    is_pdf_url = "/view/paper/" in url
    slug = (
        f"{r['filed_date'].replace('/', '-')}_{slugify(r['last'])}"
        f"_{slugify(r['first'])}_{slugify(url.rstrip('/').split('/')[-1] or 'x')}"
    )
    PTR_DIR.mkdir(parents=True, exist_ok=True)

    if is_pdf_url:
        # Paper-filed: HTML viewer with GIF page images
        page.wait_for_selector("img.filingImage, body", timeout=10000)
        html = page.content()
        out_html = PTR_DIR / f"{slug}_paper.html"
        out_html.write_text(html, encoding="utf-8")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        page_imgs = [img.get("src") for img in soup.select("img.filingImage") if img.get("src")]
        # Download the GIFs straight from the public CDN. Cheap, plain HTTP,
        # idempotent — pulling them here means the OCR pipeline doesn't need
        # to round-trip back to Akamai-protected portal pages.
        local_gif_paths = _download_paper_gifs(slug, page_imgs)
        sidecar = PTR_DIR / f"{slug}_paper.json"
        sidecar.write_text(json.dumps({
            "filing_url": url,
            "viewer_html": str(out_html),
            "page_image_urls": page_imgs,
            "page_count": len(page_imgs),
            "local_page_paths": local_gif_paths,
            "downloaded_pages": len(local_gif_paths),
        }, indent=2))
        return {**r, "saved_path": str(out_html), "file_type": "paper-scan",
                "page_count": len(page_imgs),
                "downloaded_pages": len(local_gif_paths),
                "is_html_filing": False}

    # HTML transactions table
    page.wait_for_selector("table", timeout=10000)
    html = page.content()
    out = PTR_DIR / f"{slug}.html"
    out.write_text(html, encoding="utf-8")
    return {**r, "saved_path": str(out), "file_type": "html",
            "page_count": None, "is_html_filing": True}


def run_scrape(
    *,
    date_from: str,
    date_to: str,
    headless: bool = True,
    max_filings: int | None = None,
) -> ScrapeResult:
    """Drive the full scrape flow. Returns search results + per-filing download manifest."""
    log.info("[senate_efd] scrape from=%s to=%s headless=%s", date_from, date_to, headless)
    out = ScrapeResult()
    with sync_playwright() as p:
        # Akamai fingerprints headless Chromium and blocks it.
        # `--disable-blink-features=AutomationControlled` + hiding
        # `navigator.webdriver` lets the agreement page render in headless,
        # but the search submit still trips deeper checks. Headed mode is
        # the only fully reliable path today. Tracked: see `senate-efd-host.md`.
        launch_args = ["--disable-blink-features=AutomationControlled"]
        browser = p.chromium.launch(headless=headless, args=launch_args)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            viewport={"width": 1400, "height": 900},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.new_page()
        try:
            _accept_agreement(page)
            _run_search(page, date_from=date_from, date_to=date_to, filer="Senator")
            out.search_results = _collect_results(page)
            for i, r in enumerate(out.search_results, 1):
                if max_filings and i > max_filings:
                    break
                manifest = _download_filing(page, r)
                if manifest:
                    out.downloaded.append(manifest)
                time.sleep(0.4)
        finally:
            browser.close()
    log.info("[senate_efd] downloaded %d filings", len(out.downloaded))
    return out
