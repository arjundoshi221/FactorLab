"""IBKR Flex identity -> FactorLab instrument records (links every stock to a listing)."""

from __future__ import annotations

from xml.etree.ElementTree import Element

from factorlab.sources.ibkr.instruments import (
    factorlab_symbol,
    instrument_record,
    instrument_records,
)


def row(**attributes: str) -> Element:
    base = {
        "conid": "599900800",
        "assetCategory": "STK",
        "subCategory": "COMMON",
        "symbol": "BN",
        "listingExchange": "NYSE",
        "currency": "USD",
        "isin": "CA11271J1075",
        "figi": "BBG000C9KL89",
        "description": "BROOKFIELD CORP",
    }
    return Element("OpenPosition", {**base, **attributes})


def test_a_stock_becomes_a_record_keyed_by_conid_on_its_mic():
    record = instrument_record(row().attrib)
    assert record is not None
    assert (record.ref.alias_kind, record.ref.alias_value) == ("ibkr_conid", "599900800")
    assert (record.ref.exchange_code, record.ref.country_code, record.ref.trading_symbol) == (
        "XNYS",
        "US",
        "BN",
    )
    assert (record.ref.isin, record.ref.figi, record.name) == (
        "CA11271J1075",
        "BBG000C9KL89",
        "BROOKFIELD CORP",
    )
    assert (record.product_type, record.currency) == ("common", "USD")


def test_etfs_share_classes_and_other_venues():
    etf = instrument_record(
        row(conid="604408720", symbol="BOXX", subCategory="ETF", listingExchange="BATS").attrib
    )
    assert etf is not None and (etf.product_type, etf.ref.exchange_code) == ("etf", "BATS")
    assert factorlab_symbol("MOG A") == "MOG-A" and factorlab_symbol("brk.b") == "BRK-B"
    london = instrument_record(row(listingExchange="LSEETF").attrib)
    assert london is not None and (london.ref.exchange_code, london.ref.country_code) == (
        "XLON",
        "GB",
    )


def test_options_unmapped_venues_and_bad_isins_are_left_out_or_cleaned():
    assert instrument_record(row(assetCategory="OPT").attrib) is None
    assert instrument_record(row(listingExchange="TSE").attrib) is None
    assert instrument_record(row(conid="").attrib) is None
    record = instrument_record(row(isin="").attrib)
    assert record is not None and record.ref.isin is None


def test_one_record_per_conid_preferring_the_row_with_an_isin():
    records = instrument_records(
        [row(isin=""), row(), row(conid="9160", symbol="LLY", isin="US5324571083")]
    )
    assert [(r.ref.alias_value, r.ref.isin) for r in records] == [
        ("599900800", "CA11271J1075"),
        ("9160", "US5324571083"),
    ]
