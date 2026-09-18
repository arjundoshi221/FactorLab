import io
import zipfile
from datetime import date

from factorlab.sources.political.house_clerk import (
    parse_house_filing_index,
    parse_house_ptr_text,
)
from factorlab.storage.political_clickhouse import (
    build_name_resolver,
    resolve_filing_bioguide,
)


def _house_zip(xml: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("2026FD.xml", xml)
    return payload.getvalue()


def test_house_index_keeps_only_ptr_filings():
    payload = _house_zip(
        """
        <FinancialDisclosure>
          <Member>
            <Prefix>Hon.</Prefix><Last>Example</Last><First>Ada</First>
            <Suffix/><FilingType>P</FilingType><StateDst>CA12</StateDst>
            <Year>2026</Year><FilingDate>7/1/2026</FilingDate>
            <DocID>20000001</DocID>
          </Member>
          <Member>
            <Last>Example</Last><First>Ada</First>
            <FilingType>C</FilingType><StateDst>CA12</StateDst>
            <Year>2026</Year><FilingDate>7/2/2026</FilingDate>
            <DocID>10000001</DocID>
          </Member>
        </FinancialDisclosure>
        """
    )

    filings = parse_house_filing_index(payload, 2026)

    assert len(filings) == 1
    assert filings[0]["filing_id"] == "20000001"
    assert filings[0]["filing_date"] == date(2026, 7, 1)
    assert filings[0]["state_district_raw"] == "CA12"


def test_ptr_parser_handles_wrapped_asset_metadata_and_page_duplicates():
    filing = {"filing_id": "20000001"}
    text = """
    Amazon.com, Inc. - Common Stock S (partial) 03/16/2026 03/16/2026 $1,001 - $15,000
    (AMZN) [ST]
    Filing Status: New
    Apple Inc. - Common Stock (AAPL) [ST] P 03/17/2026 03/17/2026 $15,001 - $50,000
    Berkshire Hathaway Inc. New S (partial) 03/18/2026 03/18/2026 $1,001 - $15,000
    Common Stock (BRK.B) [ST]
    Amazon.com, Inc. - Common Stock S (partial) 03/16/2026 03/16/2026 $1,001 - $15,000
    (AMZN) [ST]
    """

    trades = parse_house_ptr_text(text, filing)

    assert len(trades) == 3
    assert [trade["ticker"] for trade in trades] == ["AMZN", "AAPL", "BRK.B"]
    assert trades[0]["transaction_type"] == "sale_partial"
    assert trades[1]["transaction_type"] == "purchase"
    assert trades[1]["amount_max"] == 50_000


def test_name_resolution_matches_house_filing_to_bioguide():
    legislators = [
        {
            "id": {"bioguide": "E000001"},
            "name": {"first": "Ada", "last": "Example"},
        }
    ]
    filing = {
        "filer_first_name": "Ada M.",
        "filer_last_name": "Example",
    }

    resolver = build_name_resolver(legislators)

    assert resolve_filing_bioguide(filing, resolver) == "E000001"
