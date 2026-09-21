"""Create two-level vendor model in ref schema.

ref.vendors            — parent: company / organization providing data
ref.data_endpoints     — child: specific feed / URL pattern; FK to vendors

Replaces the freeform `source` String columns scattered across event tables.
Each fact-table row in subsequent migrations gets `endpoint_id` FK; vendor
reached via JOIN.

Canonical seed list derived from current live data (audit run 2026-05-01):
  legislator_trades.source: house_clerk_ptr, senate_efd_ptr, senate_stock_watcher_historical
  gov_contracts.source: usaspending_direct, finnhub_usa_spending
  raw_archive.source: house_clerk, senate_efd, fec, congress_gov, lda, lda_probe,
                      usaspending, senate_stock_watcher, finnhub, legislators
  market.candles_1min.source: upstox

Revision ID: 012
Revises: 011
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "ref"


def upgrade() -> None:
    # ── ref.vendors — parent dim ────────────────────────────────────────────
    op.create_table(
        "vendors",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True,
                  comment="Short canonical code: upstox, house_clerk, eodhd, …"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("vendor_type", sa.String(15), nullable=False,
                  comment="api / scrape / file / mirror / official"),
        sa.Column("homepage_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "vendor_type IN ('api','scrape','file','mirror','official')",
            name="vendor_type",
        ),
        schema=SCHEMA,
        comment="Data providers / source organizations. Parent of data_endpoints.",
    )
    op.create_index("ix_vendors_code", "vendors", ["code"], schema=SCHEMA)

    # ── ref.data_endpoints — child dim, one row per distinct feed/URL ──────
    op.create_table(
        "data_endpoints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vendor_id", sa.Integer,
                  sa.ForeignKey(f"{SCHEMA}.vendors.id"), nullable=False),
        sa.Column("code", sa.String(60), nullable=False, unique=True,
                  comment="Endpoint canonical code: house_clerk_ptr, senate_efd_ptr, …"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("endpoint_type", sa.String(15), nullable=False,
                  comment="rest / graphql / scrape / file / yaml / dump"),
        sa.Column("url_pattern", sa.String(500), nullable=True,
                  comment="Templated URL with {placeholders} where applicable"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "endpoint_type IN ('rest','graphql','scrape','file','yaml','dump')",
            name="endpoint_type",
        ),
        schema=SCHEMA,
        comment="Specific feeds / URL patterns. Each fact-table row's endpoint_id "
                "FKs here; the vendor is reached via the FK chain.",
    )
    op.create_index("ix_endpoints_code", "data_endpoints", ["code"], schema=SCHEMA)
    op.create_index("ix_endpoints_vendor", "data_endpoints", ["vendor_id"], schema=SCHEMA)

    # ── Seed vendors ────────────────────────────────────────────────────────
    vendors_seed = [
        # Market data vendors
        {"code": "upstox", "name": "Upstox", "vendor_type": "api",
         "homepage_url": "https://upstox.com",
         "notes": "Indian broker — equity/F&O OHLCV (1m, daily); Nifty50/100/500 universes."},
        {"code": "ibkr", "name": "Interactive Brokers", "vendor_type": "api",
         "homepage_url": "https://www.interactivebrokers.com",
         "notes": "Direct exchange feed; multi-region; ib_async client. Pro tier required."},
        {"code": "eodhd", "name": "EOD Historical Data", "vendor_type": "api",
         "homepage_url": "https://eodhd.com",
         "notes": "Daily OHLCV + fundamentals (paid tier). Free 20 calls/day."},
        {"code": "schwab", "name": "Charles Schwab", "vendor_type": "api",
         "homepage_url": "https://developer.schwab.com",
         "notes": "Free with brokerage account. OAuth2 (7-day refresh). Read-only by design."},
        {"code": "finnhub", "name": "Finnhub", "vendor_type": "api",
         "homepage_url": "https://finnhub.io",
         "notes": "Quotes + USA-Spending mirror + insider transactions."},
        # Political data vendors
        {"code": "house_clerk", "name": "Clerk of the U.S. House of Representatives",
         "vendor_type": "official",
         "homepage_url": "https://disclosures-clerk.house.gov",
         "notes": "STOCK Act PTRs, year-XML index, ZIP archives. Multiple endpoints."},
        {"code": "senate_efd", "name": "U.S. Senate Office of Public Records (eFD)",
         "vendor_type": "official",
         "homepage_url": "https://efdsearch.senate.gov",
         "notes": "STOCK Act PTRs (HTML + paper-scan PDFs). Akamai-blocked from cloud."},
        {"code": "senate_stock_watcher", "name": "Senate Stock Watcher (community mirror)",
         "vendor_type": "mirror",
         "homepage_url": "https://github.com/timothycarambat/senate-stock-watcher-data",
         "notes": "Frozen 2014-2019 historical Senate trades JSON dump."},
        {"code": "lda", "name": "U.S. Lobbying Disclosure Act database",
         "vendor_type": "api",
         "homepage_url": "https://lda.senate.gov",
         "notes": "House Clerk's lobbying disclosure REST API."},
        {"code": "usaspending", "name": "USAspending.gov", "vendor_type": "api",
         "homepage_url": "https://www.usaspending.gov",
         "notes": "Treasury federal contract awards. Sovereign primary source."},
        {"code": "fec", "name": "Federal Election Commission", "vendor_type": "api",
         "homepage_url": "https://api.open.fec.gov",
         "notes": "Campaign finance — committees, donations. Requires FEC_API_KEY."},
        {"code": "congress_gov", "name": "Congress.gov (Library of Congress)",
         "vendor_type": "api",
         "homepage_url": "https://api.congress.gov",
         "notes": "Bills, hearings, witnesses. Requires CONGRESS_API_KEY."},
        {"code": "unitedstates_legislators",
         "name": "unitedstates/congress-legislators (GitHub)",
         "vendor_type": "file",
         "homepage_url": "https://github.com/unitedstates/congress-legislators",
         "notes": "YAML dim: legislators-current, legislators-historical, committees-*, "
                  "committee-membership-current."},
    ]
    op.bulk_insert(
        sa.table(
            "vendors",
            sa.column("code"), sa.column("name"), sa.column("vendor_type"),
            sa.column("homepage_url"), sa.column("notes"),
            schema=SCHEMA,
        ),
        vendors_seed,
    )

    # ── Seed data_endpoints ─────────────────────────────────────────────────
    # Each existing `source` String value maps to one endpoint code.
    # Format: (vendor_code, endpoint_code, endpoint_name, endpoint_type, url_pattern, notes)
    endpoints_seed = [
        # Market endpoints
        ("upstox", "upstox",
         "Upstox (vendor-level catch-all — historical legacy from pre-redesign data)",
         "rest", None,
         "Pre-redesign rows in market.candles_1min were tagged `source='upstox'` "
         "(vendor-level). Kept as a catch-all endpoint so migration 022's "
         "backfill from source string resolves cleanly. Future writes should "
         "use the granular `upstox_intraday_1m` etc. endpoints."),
        ("upstox", "upstox_intraday_1m",
         "Upstox 1-min historical/intraday candles", "rest",
         "https://api.upstox.com/v3/historical-candle/intraday/{instrument_key}/1minute",
         "Granular endpoint for new writes. 529,035 legacy rows resolve via the "
         "vendor-level `upstox` endpoint above."),
        ("upstox", "upstox_instruments",
         "Upstox instrument master CSV", "file",
         "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
         "Daily refresh. Used to populate ref.instruments for India."),
        ("ibkr", "ibkr_intraday",
         "IBKR client-portal historical bars", "rest", None,
         "Pending Pro upgrade + Snapshot Bundle."),
        ("eodhd", "eodhd_eod",
         "EODHD daily OHLCV", "rest",
         "https://eodhd.com/api/eod/{symbol}",
         "Free tier: 20 calls/day."),
        ("schwab", "schwab_pricehistory",
         "Schwab /marketdata/v1/pricehistory", "rest",
         "https://api.schwabapi.com/marketdata/v1/pricehistory",
         "Read-only; never call /trader/v1/* endpoints."),
        # Political endpoints — match actual `source` strings used today
        ("house_clerk", "house_clerk_ptr",
         "House Clerk Periodic Transaction Reports (PDFs)", "scrape",
         "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf",
         "53,081 trade rows live; per-DocID PDFs parsed via pdfplumber."),
        ("house_clerk", "house_clerk_year_index",
         "House Clerk annual filing-index ZIP", "file",
         "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP",
         "Discovery only; XML inside lists DocIDs to fetch."),
        ("senate_efd", "senate_efd_ptr",
         "Senate eFD HTML Periodic Transaction Reports", "scrape", None,
         "2,483 trade rows live; Playwright + residential IP required."),
        ("senate_efd", "senate_efd_paper",
         "Senate eFD paper-scan PDFs (OCR pending)", "scrape", None,
         "Placeholder rows; OCR pipeline deferred."),
        ("senate_stock_watcher", "senate_stock_watcher_historical",
         "SSW frozen historical 2014-2019 JSON dump", "dump",
         "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/"
         "master/aggregate/all_transactions.json",
         "7,757 trade rows live."),
        ("lda", "lda_filings",
         "LDA quarterly /api/v1/filings/", "rest",
         "https://lda.senate.gov/api/v1/filings/",
         "25 filing rows live; full historical backfill deferred."),
        ("lda", "lda_probe",
         "LDA endpoint probe (smoke test only)", "rest", None,
         "Not used for production ingest."),
        ("usaspending", "usaspending_direct",
         "USAspending /api/v2/search/spending_by_award/", "rest",
         "https://api.usaspending.gov/api/v2/search/spending_by_award/",
         "1,110 contract rows live."),
        ("finnhub", "finnhub_usa_spending",
         "Finnhub /stock/usa-spending mirror", "rest",
         "https://finnhub.io/api/v1/stock/usa-spending",
         "3,804 contract rows live (parallel to usaspending_direct for QA)."),
        ("fec", "fec_committees",
         "FEC /committees/ endpoint", "rest",
         "https://api.open.fec.gov/v1/committees/",
         "Pending FEC_API_KEY."),
        ("fec", "fec_donations",
         "FEC /schedule_a/ contributions", "rest",
         "https://api.open.fec.gov/v1/schedule_a/",
         "Pending FEC_API_KEY."),
        ("congress_gov", "congress_gov_bills",
         "Congress.gov /bill endpoint", "rest",
         "https://api.congress.gov/v3/bill",
         "Pending CONGRESS_API_KEY; 332 raw_archive rows from probe."),
        ("congress_gov", "congress_gov_hearings",
         "Congress.gov /hearing endpoint", "rest",
         "https://api.congress.gov/v3/hearing", None),
        ("unitedstates_legislators", "legislators_yaml",
         "unitedstates/congress-legislators YAML dim refresh", "file",
         "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
         "main/{filename}.yaml",
         "Refreshes legislators, terms, committees, assignments dims daily."),
        ("unitedstates_legislators", "legislators",
         "Legislators YAML refresh (legacy short code)", "file", None,
         "Catch-all matching the pre-redesign `legislators` source string in "
         "alt_political.raw_archive (24 rows). Future writes should use the "
         "granular `legislators_yaml` endpoint above."),
    ]
    # Resolve vendor codes to ids inline via a SQL INSERT...SELECT
    for vendor_code, ep_code, ep_name, ep_type, url, notes in endpoints_seed:
        op.execute(sa.text("""
            INSERT INTO ref.data_endpoints (vendor_id, code, name, endpoint_type,
                                            url_pattern, notes)
            SELECT v.id, :code, :name, :etype, :url, :notes
            FROM ref.vendors v
            WHERE v.code = :vcode
        """).bindparams(
            vcode=vendor_code, code=ep_code, name=ep_name,
            etype=ep_type, url=url, notes=notes,
        ))


def downgrade() -> None:
    op.drop_index("ix_endpoints_vendor", table_name="data_endpoints", schema=SCHEMA)
    op.drop_index("ix_endpoints_code", table_name="data_endpoints", schema=SCHEMA)
    op.drop_table("data_endpoints", schema=SCHEMA)
    op.drop_index("ix_vendors_code", table_name="vendors", schema=SCHEMA)
    op.drop_table("vendors", schema=SCHEMA)
