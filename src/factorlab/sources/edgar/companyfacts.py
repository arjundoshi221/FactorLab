"""XBRL company facts / concept / frames API (data.sec.gov/api/xbrl).

The XBRL endpoints return pre-parsed JSON — much easier than parsing raw XBRL
XML out of 10-K/10-Q filings.

    from factorlab.sources.edgar import EdgarClient, get_company_facts
    client = EdgarClient()
    facts = get_company_facts(client, cik=320193)
    rev = get_company_concept(client, cik=320193, tag="Revenues")
    period = get_frame(client, tag="Assets", year=2024, quarter=3)
"""

from __future__ import annotations

from typing import Any

from factorlab.sources.edgar.client import EdgarClient, cik_padded


def get_company_facts(client: EdgarClient, cik: int | str) -> dict[str, Any]:
    """All XBRL facts ever reported by a filer."""
    padded = cik_padded(cik)
    return client.get_data(f"/api/xbrl/companyfacts/CIK{padded}.json").json()


def get_company_concept(
    client: EdgarClient,
    cik: int | str,
    tag: str,
    *,
    taxonomy: str = "us-gaap",
) -> dict[str, Any]:
    """One XBRL concept (e.g. 'Revenues') across every filing by one filer."""
    padded = cik_padded(cik)
    return client.get_data(
        f"/api/xbrl/companyconcept/CIK{padded}/{taxonomy}/{tag}.json"
    ).json()


def get_frame(
    client: EdgarClient,
    tag: str,
    year: int,
    quarter: int | None = None,
    *,
    taxonomy: str = "us-gaap",
    unit: str = "USD",
    instantaneous: bool = True,
) -> dict[str, Any]:
    """One XBRL concept across all filers for one period.

    Period key:
      - Instantaneous (balance-sheet items): ``CY{year}Q{n}I`` — e.g. CY2024Q3I
      - Duration (flow items):               ``CY{year}Q{n}``  — e.g. CY2024Q3
      - Full year:                           ``CY{year}``      — e.g. CY2024
    """
    if quarter is None:
        period = f"CY{year}"
    elif instantaneous:
        period = f"CY{year}Q{quarter}I"
    else:
        period = f"CY{year}Q{quarter}"
    return client.get_data(
        f"/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
    ).json()
