"""Canonical record validation (docs/architecture/07 §5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from factorlab.ingest.datasets import (
    DATASETS,
    BarRecord,
    Capabilities,
    ContractRecord,
    FetchUnit,
    InstrumentRecord,
    InstrumentRef,
    SeriesWindow,
    dataset,
)

NOW = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
REF = InstrumentRef("upstox_instrument_key", "NSE_EQ|INE002A01018", "NSE", "RELIANCE", "IN",
                    isin="INE002A01018")


def _bar(**overrides):
    values = {"instrument": REF, "resolution": "1min", "bar_time": NOW,
              "open": Decimal(10), "high": Decimal(11), "low": Decimal(9),
              "close": Decimal("10.5"), "volume": 100, **overrides}
    return BarRecord(**values)


def test_instrument_ref_requires_alias_or_natural_key():
    InstrumentRef("", "", "NSE", "RELIANCE", "IN")  # natural-key-only hint is fine
    InstrumentRef("", "", "", "RELIANCE", "IN")  # country-scoped ticker hint is fine too
    with pytest.raises(ValueError):
        InstrumentRef("", "", "NSE", "", "IN")
    with pytest.raises(ValueError):
        InstrumentRef("upstox_instrument_key", "", "NSE", "RELIANCE", "IN")
    with pytest.raises(ValueError):
        InstrumentRef("k", "v", "NSE", "RELIANCE", "IND")
    with pytest.raises(ValueError):
        InstrumentRef("k", "v", "NSE", "RELIANCE", "IN", isin="SHORT")


def test_bar_record_enforces_utc_decimal_and_ohlc():
    assert _bar().close == Decimal("10.5")
    with pytest.raises(ValueError):
        _bar(bar_time=datetime(2026, 9, 24, 4, 0))
    with pytest.raises(TypeError):
        _bar(open=10.0)
    with pytest.raises(ValueError):
        _bar(low=Decimal(12))
    with pytest.raises(ValueError):
        _bar(resolution="1m")
    with pytest.raises(ValueError):
        _bar(volume=-1)
    with pytest.raises(TypeError):
        _bar(volume=True)
    with pytest.raises(ValueError):
        _bar(session="lunch")


def test_bar_record_allows_missing_prices():
    bar = _bar(open=None, high=None, low=None, close=None, volume=None)
    assert bar.close is None


def test_reference_records_validate_vocabulary():
    InstrumentRecord(ref=REF, name="Reliance", product_type="common", currency="INR",
                     tick_size=Decimal(5))
    with pytest.raises(ValueError):
        InstrumentRecord(ref=REF, name="x", product_type="stock", currency="INR")
    with pytest.raises(ValueError):
        InstrumentRecord(ref=InstrumentRef("", "", "NSE", "RELIANCE", "IN"), name="x",
                         product_type="common", currency="INR")
    contract_ref = InstrumentRef("upstox_instrument_key", "NSE_FO|1", "NSE", "RELIANCE FUT",
                                 "IN")
    ContractRecord(ref=contract_ref, underlying=REF, product_type="single_stock_future",
                   expiry=date(2026, 9, 29))
    with pytest.raises(ValueError):
        ContractRecord(ref=contract_ref, underlying=REF, product_type="option",
                       expiry=date(2026, 9, 29), right="C")


def test_series_window_and_fetch_unit_are_utc():
    with pytest.raises(ValueError):
        SeriesWindow(REF, NOW, NOW)
    with pytest.raises(ValueError):
        FetchUnit("u", "upstox:x", start=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        Capabilities(markets=frozenset())
    with pytest.raises(ValueError):
        Capabilities(markets=frozenset({"IND"}), resolutions=frozenset({"1m"}))


def test_catalogue_is_consistent():
    for spec in DATASETS.values():
        assert spec.id == dataset(spec.id).id
        assert spec.record_types
        assert hasattr(spec.sink_protocol, spec.sink_method)
    with pytest.raises(KeyError):
        dataset("market.nope")
