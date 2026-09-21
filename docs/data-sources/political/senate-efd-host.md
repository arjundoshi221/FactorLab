# Senate eFD — host-only

**Owner:** ritchie · **Last updated:** 2026-06-17

## Why this is special

The Senate eFD (Electronic Financial Disclosure) site is behind Akamai with a residential-IP allowlist. Requests from any cloud provider (AWS, GCP, Railway, anything with a datacenter ASN) get 403/blocked. Worse: the search form is dynamic JavaScript, so the scraper needs **Playwright** with a real browser instance.

Both constraints mean **the Senate eFD ingest cannot containerize and cannot run on Railway.** It runs on the Windows host directly, on the user's residential connection, with Playwright installed in the local Conda env.

This is acknowledged in [`E:\AGENTS\memory\projects\factorlab\decisions.md`](file:///E:/AGENTS/memory/projects/factorlab/decisions.md) ADR-001 as the single explicit exception to "everything could containerize later."

## What runs

- `factorlab.countries.us.political.senate_efd.scraper.run_scrape(...)` — Playwright session: form search by date range, paginate, fetch each filing detail
- `factorlab.countries.us.political.senate_efd.parser.parse_html(html)` — extract transactions from a single filing HTML

Invoked by:
- **Daily:** `scripts/us/political/us_political_daily.py --mode daily` (7-day rolling window, headless — currently blocked, see "Failure modes")
- **Weekly:** `scripts/us/political/us_political_daily.py --mode weekly` (broader sweep)
- **Manual / backfill (headed — the only reliable path today):**

  ```powershell
  $ts = Get-Date -Format "yyyyMMdd_HHmmss"
  & "C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts/us/political/us_political_backfill.py --phase 2 --house-clerk-limit 0 --senate-efd-headed --senate-efd-from 2026-06-10 --senate-efd-to (Get-Date -Format "yyyy-MM-dd") *>&1 | Tee-Object -FilePath "logs/factlab_senate_efd_catchup_$ts.log"
  ```

  `--house-clerk-limit 0` skips House Clerk so this doesn't duplicate work
  the daily orchestrator already did. Adjust `--senate-efd-from` to cover
  the window you missed.

## Required local setup

```powershell
# Once per machine
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" -m pip install -e ".[political]"
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" -m playwright install chromium
```

The `[political]` extra brings `playwright` + `pdfplumber`. Playwright then downloads its browser bundle. This is a ~200MB one-time install.

## Failure modes

| Symptom                                                       | Likely cause                                                  | Fix                                                                                  |
|---------------------------------------------------------------|---------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `playwright._impl._api_types.Error: net::ERR_HTTP_RESPONSE_CODE_FAILURE` (403) | You're on VPN / cellular / cloud egress                       | Disconnect VPN; verify with `(Invoke-WebRequest "https://ifconfig.io").Content`     |
| `Locator.check: Timeout 30000ms exceeded` on `input[type='checkbox']` in **headless** mode only (headed works) | Akamai serves a challenge to headless Chromium; agreement-page checkbox never renders | Run with `--senate-efd-headed` as workaround. Permanent fix needs `playwright-stealth` / UA spoofing / passing the challenge programmatically. Observed 2026-06-17. |
| HTML parses empty                                             | Akamai now serves the challenge page; Playwright loaded too fast | Bump scraper timeout in `run_scrape(...)`; check for new HTML structure              |
| Filings appear in search but detail-page fetch 404s           | filing pulled / expired                                       | Skip; log filing_id; reconcile manually                                              |
| Daily orchestrator marks senate_efd as `warn` (non-fatal)     | Expected: the daily allowlist treats senate_efd as non-fatal | The next hourly retry picks it up if state has cooled                                 |

The daily orchestrator's `NON_FATAL_SOURCES` set in `scripts/us/political/us_political_daily.py` includes `senate_efd` — a Senate eFD scrape failure does not fail the whole political run. House Clerk is **fatal** because it's the spine; eFD is supplementary.

## Database tag

Ingested rows land in `alt_political_us.legislator_trades` with `source = 'senate_efd_ptr'`. Always query `alt_political_us.legislator_trades_dedup` (migration 028) for analytics rather than the raw table — it handles cross-source overlap with `senate_stock_watcher_historical` (2019–2020).

## Why we don't move it off-host

The two natural workarounds — residential proxy + cloud Playwright — each have a fatal flaw:

- **Residential proxy**: monthly cost ($75+) for what is a low-volume scrape, plus introduces a single point of failure (proxy provider).
- **Cloud Playwright**: even the residential-flavoured ASN ranges trip Akamai inconsistently.

Local host is fine: the scrape runs in ~5 min/day, the user's residential IP is stable, the Conda env already has Playwright. Cost = 0.
