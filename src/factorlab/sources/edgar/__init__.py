"""SEC EDGAR data source — filings, XBRL, full-text search.

Read-only wrapper over the three EDGAR hosts:
    - www.sec.gov     — Archives, full-index, RSS
    - data.sec.gov    — submissions, XBRL companyfacts/concept/frames
    - efts.sec.gov    — full-text search

See docs/data-sources/20-edgar-sec-filings.md for scope and roadmap.
"""

from factorlab.sources.edgar.client import EdgarClient
from factorlab.sources.edgar.companyfacts import (
    get_company_concept,
    get_company_facts,
    get_frame,
)
from factorlab.sources.edgar.filings import (
    filing_base_url,
    filing_index_url,
    form_rss_url,
    get_daily_filings,
    get_quarterly_index,
    parse_idx,
)
from factorlab.sources.edgar.search import search
from factorlab.sources.edgar.submissions import get_submissions
from factorlab.sources.edgar.tickers import get_ticker_cik_map, resolve_cik

__all__ = [
    "EdgarClient",
    "filing_base_url",
    "filing_index_url",
    "form_rss_url",
    "get_company_concept",
    "get_company_facts",
    "get_daily_filings",
    "get_frame",
    "get_quarterly_index",
    "get_submissions",
    "get_ticker_cik_map",
    "parse_idx",
    "resolve_cik",
    "search",
]
