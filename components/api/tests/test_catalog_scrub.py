"""Error text shown in the catalog never carries a vendor credential."""

from factorlab.components.api.catalog_query import scrub_text


def test_eodhd_api_token_is_scrubbed():
    text, _ = scrub_text(
        "GET https://eodhd.com/api/eod/AAPL.US?api_token=s3cret&fmt=json", limit=200
    )
    assert "s3cret" not in text and "api_token=[redacted]&fmt=json" in text
