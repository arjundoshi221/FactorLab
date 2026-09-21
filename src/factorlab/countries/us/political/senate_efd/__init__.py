"""Senate eFD ingestion (Playwright local — Akamai blocks cloud IPs).

Flow:
    scraper.run()  → accept T&C → date-range search → download HTMLs/paper-viewers
    parser.parse() → HTML transactions → trade rows
    ingest        → legislator_trades + raw_archive

Must run from your local Windows machine; production cron via Task Scheduler.
"""

from factorlab.countries.us.political.senate_efd.ingest import ingest_senate_efd  # noqa: F401

__all__ = ["ingest_senate_efd"]
