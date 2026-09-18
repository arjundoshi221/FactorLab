import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from factorlab.sources.eodhd.us_universe import (
    canonical_symbol,
    normalize_bulk,
    normalize_daily,
    normalize_master,
    schwab_symbol,
)

spec = importlib.util.spec_from_file_location(
    "us_full_runner", Path(__file__).parents[1] / "scripts/factlab_us_clickhouse.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def master_item(code, *, exchange="NASDAQ", kind="Common Stock", currency="USD"):
    return {"Code": code, "Name": f"{code} Incorporated", "Exchange": exchange,
            "Type": kind, "Currency": currency, "Isin": None}


def test_master_keeps_selected_common_stock_venues_and_adrs():
    result = normalize_master([
        master_item("AAPL"), master_item("BABA", exchange="NYSE"),
        master_item("SMALL", exchange="NYSE MKT"), master_item("SPY", kind="ETF"),
        master_item("OTC", exchange="PINK"), master_item("CAD", currency="CAD"),
    ])
    assert [item["symbol"] for item in result] == ["AAPL", "BABA", "SMALL"]
    assert [item["exchange_code"] for item in result] == ["XNAS", "XNYS", "XASE"]


def test_symbol_mapping_and_daily_validation():
    assert canonical_symbol("brk/b.us") == "BRK-B"
    assert schwab_symbol("BRK-B") == "BRK/B"
    frame = normalize_daily([{"date": "2026-09-14", "open": 10, "high": 12,
                              "low": 9, "close": 11, "adjusted_close": 10.5,
                              "volume": 100}])
    assert frame.iloc[0]["trade_date"] == date(2026, 9, 14)
    assert frame.iloc[0]["adj_close"] == 10.5
    with pytest.raises(ValueError):
        normalize_daily([{"date": "2026-09-14", "open": 10, "high": 8,
                          "low": 9, "close": 11, "volume": 100}])


def test_bulk_is_scoped_to_active_master_and_deduplicated():
    identifier = uuid4()
    records = [{"code": "AAPL", "date": "2026-09-14", "open": 10, "high": 12,
                "low": 9, "close": 11, "adjusted_close": 10.5, "volume": 100},
               {"code": "OTC", "date": "2026-09-14", "open": 1, "high": 1,
                "low": 1, "close": 1, "volume": 1}]
    result = normalize_bulk(records, {"AAPL": identifier}, "raw")
    assert len(result) == 1
    assert result[0]["instrument_id"] == identifier
    assert result[0]["raw_id"] == "raw"


def test_bulk_skips_one_malformed_row_without_losing_snapshot():
    identifiers = {symbol: uuid4() for symbol in ("AAPL", "MSFT")}
    records = [
        {"code": "AAPL", "date": "2026-09-14", "open": 10, "high": 12,
         "low": 9, "close": 11, "volume": 100},
        {"code": "MSFT", "date": "2026-09-14", "open": 10, "high": 8,
         "low": 9, "close": 11, "volume": 100},
    ]
    result = normalize_bulk(records, identifiers)
    assert [item["symbol"] for item in result] == ["AAPL"]


def test_pending_daily_includes_incomplete_stale_and_error_states():
    now = runner.datetime(2026, 9, 15, 22, tzinfo=runner.UTC)
    target_day = runner.latest_completed(now)
    target = runner.bounds(target_day)[1]
    identifiers = [uuid4() for _ in range(4)]
    items = [{"instrument_id": identifier, "symbol": str(index)}
             for index, identifier in enumerate(identifiers)]
    states = {
        identifiers[0]: {"history_complete": False, "checked_through": None, "error": None},
        identifiers[1]: {"history_complete": True, "checked_through": target - runner.timedelta(days=1), "error": None},
        identifiers[2]: {"history_complete": True, "checked_through": target, "error": "retry"},
        identifiers[3]: {"history_complete": True, "checked_through": target, "error": None},
    }
    assert [item["instrument_id"] for item in runner.pending_daily(items, states, target_day)] == identifiers[:3]


def test_sync_full_universe_activates_every_daily_series(monkeypatch):
    monkeypatch.setattr(runner, "MINIMUM_MASTER_SIZE", 1)
    storage = Mock()
    storage.active_reference_count.return_value = 0
    storage.sync_reference_master.return_value = {"AAPL": uuid4(), "BABA": uuid4()}
    eodhd = SimpleNamespace(
        get_exchange_symbols=Mock(return_value=[master_item("AAPL"), master_item("BABA", exchange="NYSE")]),
        last_raw_id="raw",
    )
    result = runner.sync_full_universe(storage, eodhd)
    assert len(result) == 2
    assert storage.sync_expected_series.call_args.kwargs == {
        "source": "eodhd", "universe": "us_listed_equities", "resolution": "daily"}
    storage.finish_ingestion_run.assert_called_once()


def test_liquid_tier_skips_unresolved_schwab_symbols(monkeypatch):
    monkeypatch.setattr(runner, "MINUTE_TIER_SIZE", 2)
    identifiers = [uuid4() for _ in range(3)]
    storage = Mock()
    storage.liquid_candidates.return_value = [
        {"instrument_id": identifier, "symbol": symbol}
        for identifier, symbol in zip(identifiers, ["BAD", "AAPL", "MSFT"], strict=True)]
    client = Mock()
    client.instrument.side_effect = [ValueError("missing"), ({}, "raw"), ({}, "raw")]
    items = [{"instrument_id": identifier, "symbol": symbol, "provider_symbol": f"{symbol}.US"}
             for identifier, symbol in zip(identifiers, ["BAD", "AAPL", "MSFT"], strict=True)]
    result = runner.select_minute_tier(storage, client, items)
    assert [item[0] for item in result] == ["AAPL", "MSFT"]
    assert storage.sync_expected_series.call_args.kwargs == {
        "source": "schwab", "universe": "us_liquid_250", "resolution": "1min"}
