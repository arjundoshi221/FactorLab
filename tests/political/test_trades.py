"""Unit tests for the political trades foundation module (`_trades.py`).

Pure-function tests — no DB connection required.
"""

from __future__ import annotations

from datetime import date

import pytest

from factorlab.countries.us.political._trades import (
    AMOUNT_BUCKETS,
    OWNER_TEXT_TO_FILER_TYPE,
    TX_TYPE_CODE_TO_NAME,
    TX_TYPE_TEXT_TO_NAME,
    dedup_trades,
    parse_amount,
    parse_iso_date,
    parse_us_date,
    parse_us_or_iso_date,
)


# ── parse_amount: 10 STOCK Act buckets + tolerant variants ─────────────────


@pytest.mark.parametrize("s, expected", [
    ("$1,001 - $15,000",         (1001, 15000, 8000)),
    ("$15,001 - $50,000",        (15001, 50000, 32500)),
    ("$50,001 - $100,000",       (50001, 100000, 75000)),
    ("$100,001 - $250,000",      (100001, 250000, 175000)),
    ("$250,001 - $500,000",      (250001, 500000, 375000)),
    ("$500,001 - $1,000,000",    (500001, 1000000, 750000)),
    ("$1,000,001 - $5,000,000",  (1000001, 5000000, 3000000)),
    ("$5,000,001 - $25,000,000", (5000001, 25000000, 15000000)),
    ("$25,000,001 - $50,000,000", (25000001, 50000000, 37500000)),
    ("Over $50,000,000",         (50000001, None, 50000001)),
])
def test_parse_amount_canonical_buckets(s, expected):
    assert parse_amount(s) == expected


@pytest.mark.parametrize("s, expected", [
    ("",     (None, None, None)),
    (None,   (None, None, None)),
    ("garbage text", (None, None, None)),
])
def test_parse_amount_returns_none_on_unrecognized(s, expected):
    """Strict-NULL: caller's responsibility to handle None."""
    assert parse_amount(s) == expected


def test_parse_amount_tolerates_whitespace_and_newlines():
    # House Clerk PDFs sometimes wrap amount across lines
    assert parse_amount("$1,001 -\n  $15,000") == (1001, 15000, 8000)


def test_parse_amount_tolerates_missing_second_dollar_sign():
    # Senate eFD HTML sometimes drops the trailing $
    assert parse_amount("$15,001 - 50,000") == (15001, 50000, 32500)


def test_parse_amount_handles_over_pattern_case_insensitive():
    """The lowercase 'over' goes through the tolerant regex fallback (not the
    canonical dict). The regex uses the captured literal as `lo`, so it
    returns 50_000_000 instead of the canonical-bucket 50_000_001 that
    `Over $50,000,000` (capital O, exact dict hit) returns. The +1 asymmetry
    is documented behavior — STOCK Act bucket semantics: 'Over $50M' really
    means $50,000,001-or-more, but the lenient parser preserves the captured
    number as a floor."""
    assert parse_amount("over $50,000,000") == (50000000, None, 50000000)
    # Capital O takes the canonical bucket path:
    assert parse_amount("Over $50,000,000") == (50000001, None, 50000001)


def test_parse_amount_buckets_dict_count():
    """Sanity: the canonical bucket set is exactly 10 entries."""
    assert len(AMOUNT_BUCKETS) == 10


# ── date parsers ───────────────────────────────────────────────────────────


def test_parse_us_date():
    assert parse_us_date("12/19/2019") == date(2019, 12, 19)
    assert parse_us_date("1/5/2024") == date(2024, 1, 5)  # single-digit month/day
    assert parse_us_date("garbage") is None
    assert parse_us_date("") is None
    assert parse_us_date(None) is None


def test_parse_iso_date():
    assert parse_iso_date("2019-12-19") == date(2019, 12, 19)
    assert parse_iso_date("garbage") is None
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None


def test_parse_us_or_iso_date():
    assert parse_us_or_iso_date("12/19/2019") == date(2019, 12, 19)
    assert parse_us_or_iso_date("2019-12-19") == date(2019, 12, 19)
    assert parse_us_or_iso_date("garbage") is None


# ── strict-NULL on unknown mappings ────────────────────────────────────────


def test_tx_type_text_to_name_returns_none_on_unknown():
    """Phase G2 strict-NULL fix: SSW/eFD must NOT default 'N/A' to 'purchase'."""
    assert TX_TYPE_TEXT_TO_NAME.get("Purchase") == "purchase"
    assert TX_TYPE_TEXT_TO_NAME.get("Sale (Full)") == "sale_full"
    assert TX_TYPE_TEXT_TO_NAME.get("N/A") is None
    assert TX_TYPE_TEXT_TO_NAME.get("garbage") is None


def test_tx_type_code_to_name_strict():
    assert TX_TYPE_CODE_TO_NAME["P"] == "purchase"
    assert TX_TYPE_CODE_TO_NAME["S"] == "sale_full"
    assert TX_TYPE_CODE_TO_NAME["E"] == "exchange"
    # Unknown codes don't have an entry — caller's .get() returns None
    assert TX_TYPE_CODE_TO_NAME.get("X") is None


def test_owner_text_to_filer_type_self_synonyms():
    """Multiple ways to say 'self' all map to 'self'."""
    for s in ("Self", "self", "N/A", "--", ""):
        assert OWNER_TEXT_TO_FILER_TYPE[s] == "self"


# ── dedup_trades: last-wins within batch ───────────────────────────────────


def _trade(filing_id="1", asset="X", tx_type="purchase", amount="$1k", **extra):
    """Helper: build a row dict with all CONFLICT_KEYS populated."""
    return {
        "endpoint_id": 1,
        "country_code": "US",
        "chamber": "house",
        "filing_id": filing_id,
        "transaction_date": date(2024, 1, 1),
        "asset_name_raw": asset,
        "transaction_type": tx_type,
        "amount_str": amount,
        **extra,
    }


def test_dedup_trades_no_duplicates():
    rows = [_trade("1", "X"), _trade("2", "Y"), _trade("3", "Z")]
    assert len(dedup_trades(rows)) == 3


def test_dedup_trades_collapses_duplicates_last_wins():
    """When two rows share the conflict-key tuple, the LAST one wins."""
    rows = [
        _trade("1", "X", marker="first"),
        _trade("1", "X", marker="second"),
        _trade("1", "X", marker="third"),
        _trade("2", "Y", marker="other"),
    ]
    out = dedup_trades(rows)
    assert len(out) == 2
    # Find the X row and confirm last-wins
    x = [r for r in out if r["filing_id"] == "1"][0]
    assert x["marker"] == "third"


def test_dedup_trades_preserves_distinct_endpoints():
    """Phase E: endpoint_id is part of the conflict key — same trade from
    different endpoints is NOT a duplicate."""
    r1 = _trade("1", "X", endpoint_id=1)
    r2 = _trade("1", "X", endpoint_id=2)  # same trade, different feed
    assert len(dedup_trades([r1, r2])) == 2


def test_dedup_trades_empty_input():
    assert dedup_trades([]) == []
