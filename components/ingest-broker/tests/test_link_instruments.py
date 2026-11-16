"""Backfill linking of IBKR conids from stored Flex statement identity."""

from __future__ import annotations

from factorlab.components.ingest_broker.cli import COMMANDS
from factorlab.components.ingest_broker.link_instruments import link, records_from
from factorlab.ingest.memory import InMemorySink


def stock(conid: str, symbol: str, venue: str = "NYSE", **extra: str) -> dict[str, str]:
    return {
        "conid": conid,
        "symbol": symbol,
        "assetCategory": "STK",
        "listingExchange": venue,
        "currency": "USD",
        "description": f"{symbol} INC",
        "subCategory": "COMMON",
        **extra,
    }


IDENTITIES = [
    stock("80410784", "SMFG", subCategory="ADR", isin="US86562M2098"),
    stock("4815747", "MOG A", isin="US6153942023"),
    stock("265598", "AAPL", "NASDAQ", isin="US0378331005"),
    stock("1", "FOO", "PINK"),  # unmapped venue: skipped
    {"conid": "2", "symbol": "USD.SGD", "assetCategory": "CASH", "listingExchange": "IDEALPRO"},
]


def test_records_skip_unmapped_venues_and_non_stocks():
    records = records_from(IDENTITIES)
    assert [(r.ref.alias_value, r.ref.trading_symbol, r.product_type) for r in records] == [
        ("80410784", "SMFG", "adr"),
        ("4815747", "MOG-A", "common"),
        ("265598", "AAPL", "common"),
    ]


def test_link_mints_then_rerun_is_a_no_op():
    sink = InMemorySink()
    records, result = link(sink, IDENTITIES)
    assert len(records) == 3
    assert (result.rows_written, result.unresolved) == (3, 0)
    for record in records:
        assert sink.resolve_listing(record.ref) is not None
    _, again = link(sink, IDENTITIES)
    assert (again.rows_written, again.unresolved, again.resolved) == (0, 0, 3)


def test_link_with_nothing_stored_still_records_a_run():
    sink = InMemorySink()
    records, result = link(sink, [])
    assert records == [] and result.rows_written == 0


def test_command_is_registered():
    assert "link-instruments" in COMMANDS
