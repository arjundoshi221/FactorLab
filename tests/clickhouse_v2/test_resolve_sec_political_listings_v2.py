from datetime import UTC, datetime

from scripts.resolve_sec_political_listings_v2 import (
    archive_row,
    canonical_ids,
    eligible_sec_listing,
    name_score,
)


def _master():
    return {"cik": 320193, "name": "Apple Inc.", "ticker": "AAPL", "exchange": "Nasdaq"}


def _submission():
    return {
        "cik": 320193,
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "stateOfIncorporation": "CA",
        "stateOfIncorporationDescription": "CA",
    }


def test_sec_candidate_requires_name_ticker_exchange_and_us_incorporation():
    assert eligible_sec_listing("AAPL", ["Apple Inc. Common Stock"], [_master()], _submission())
    assert eligible_sec_listing("AAPL", ["Other issuer"], [_master()], _submission()) is None
    assert eligible_sec_listing("AAPL", ["Apple"], [_master(), _master()], _submission()) is None
    assert eligible_sec_listing(
        "AAPL", ["Apple"], [_master()], {**_submission(), "exchanges": ["NYSE"]}
    ) is None
    assert eligible_sec_listing(
        "AAPL", ["Apple"], [_master()],
        {**_submission(), "stateOfIncorporation": "L2",
         "stateOfIncorporationDescription": "Ireland"},
    ) is None


def test_sec_ids_and_archive_ids_are_deterministic():
    first = canonical_ids(320193, "AAPL", "NASDAQ")
    assert first == canonical_ids(320193, "AAPL", "NASDAQ")
    assert len(set(first)) == 3
    assert first[2] != canonical_ids(320193, "AAPL", "NYSE")[2]
    now = datetime(2026, 9, 23, tzinfo=UTC)
    row = archive_row("https://example.test/data", b"{}", {}, now, "key", "submissions")
    assert row[0] == archive_row(
        "https://example.test/data", b"{}", {}, now, "key", "submissions"
    )[0]
    assert row[12] == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def test_name_normalization_handles_filing_type_words():
    assert name_score("Apple Inc. Common Stock", "Apple Inc.") == 1.0
