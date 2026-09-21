import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from factorlab.sources.eodhd.configured_universe import (
    UniverseConfig,
    component_symbols,
    resolve_universe,
)
from factorlab.storage.us_clickhouse import USStorage

spec = importlib.util.spec_from_file_location(
    "us_universe_runner", Path(__file__).parents[1] / "scripts/factlab_us_universe.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def config(**changes):
    payload = {
        "version": 1,
        "name": "us_configured",
        "provider": "eodhd",
        "refresh_interval_minutes": 1440,
        "indexes": [
            {"symbol": "first.indx", "minimum_constituents": 2},
            {"symbol": "SECOND.INDX", "minimum_constituents": 1},
        ],
        "extra_symbols": ["aapl.us", "BRK-B"],
    }
    payload.update(changes)
    return UniverseConfig.model_validate(payload)


def master(code, *, exchange="NASDAQ", kind="Common Stock", currency="USD"):
    return {"Code": code, "Name": code, "Exchange": exchange, "Type": kind,
            "Currency": currency, "Isin": None}


def test_config_normalizes_extras_and_rejects_invalid_shapes():
    parsed = config(extra_symbols=["AAPL", "aapl.us", "brk-b"])
    assert parsed.extra_symbols == ["AAPL", "BRK-B"]
    assert parsed.indexes[0].symbol == "FIRST.INDX"
    with pytest.raises(ValidationError):
        config(version=2)
    with pytest.raises(ValidationError):
        config(indexes=[], extra_symbols=[])
    with pytest.raises(ValidationError):
        config(indexes=[{"symbol": "SPY.US", "minimum_constituents": 1}])


def test_multiple_indexes_and_extras_form_sorted_deduplicated_union():
    client = Mock()
    client.get_exchange_symbols.return_value = [
        master("AAPL"), master("MSFT"), master("BRK-B", exchange="NYSE")]
    client.get_index_components.side_effect = [
        {"0": {"Code": "AAPL.US"}, "1": {"Code": "BRK-B"}},
        [{"Code": "MSFT"}],
    ]
    full, resolved = resolve_universe(config(), client)
    assert [item["symbol"] for item in full] == ["AAPL", "BRK-B", "MSFT"]
    assert [item["symbol"] for item in resolved] == ["AAPL", "BRK-B", "MSFT"]


def test_minimum_constituents_and_malformed_components_are_rejected():
    index = config().indexes[0]
    with pytest.raises(ValueError, match="minimum is 2"):
        component_symbols([{"Code": "AAPL"}], index)
    with pytest.raises(ValueError, match="malformed"):
        component_symbols({"message": "Unauthorized"}, index)


def test_non_us_non_common_and_non_usd_symbols_cannot_resolve():
    client = Mock()
    client.get_exchange_symbols.return_value = [
        master("AAPL"), master("SHOP", currency="CAD"),
        master("SPY", kind="ETF"), master("OTC", exchange="PINK")]
    client.get_index_components.side_effect = [
        [{"Code": "AAPL"}, {"Code": "SHOP"}], [{"Code": "SPY"}]]
    with pytest.raises(ValueError, match="not active USD US common stocks"):
        resolve_universe(config(extra_symbols=[]), client)


def test_failed_refresh_preserves_expected_series_and_reports_unhealthy(monkeypatch):
    monkeypatch.setattr(runner, "MINIMUM_MASTER_SIZE", 1)
    storage = Mock()
    storage.active_reference_count.return_value = 0
    client = SimpleNamespace(
        get_exchange_symbols=Mock(return_value=[master("AAPL")]),
        get_index_components=Mock(side_effect=RuntimeError("unauthorized")),
        last_exchange_raw_id="master-raw",
    )
    with pytest.raises(RuntimeError):
        runner.sync_once(config(indexes=[{"symbol": "FIRST.INDX",
                                         "minimum_constituents": 1}],
                                extra_symbols=[]), storage, client)
    storage.sync_reference_master.assert_not_called()
    storage.sync_expected_series.assert_not_called()
    storage.source_status.assert_called_with(
        "error", "Universe refresh failed: unauthorized", source="universe")


def test_success_syncs_full_reference_master_and_only_publishes_union(monkeypatch):
    monkeypatch.setattr(runner, "MINIMUM_MASTER_SIZE", 1)
    identifiers = {symbol: uuid4() for symbol in ("AAPL", "MSFT", "OTHER")}
    storage = Mock()
    storage.active_reference_count.return_value = 0
    storage.sync_reference_master.return_value = identifiers
    client = SimpleNamespace(
        get_exchange_symbols=Mock(return_value=[master(symbol) for symbol in identifiers]),
        get_index_components=Mock(return_value={"a": {"Code": "MSFT"}}),
        last_exchange_raw_id="master-raw",
    )
    result = runner.sync_once(
        config(indexes=[{"symbol": "FIRST.INDX", "minimum_constituents": 1}],
               extra_symbols=["AAPL"]), storage, client)
    assert [item["symbol"] for item in result] == ["AAPL", "MSFT"]
    assert len(storage.sync_reference_master.call_args.args[0]) == 3
    published = storage.sync_expected_series.call_args.args[0]
    assert [item["symbol"] for item in published] == ["AAPL", "MSFT"]
    assert storage.sync_expected_series.call_args.kwargs == {
        "source": "eodhd", "universe": "us_configured", "resolution": "daily"}


def test_expected_series_activates_additions_and_deactivates_removals():
    removed, retained, added = uuid4(), uuid4(), uuid4()
    client = Mock()
    client.query.return_value = SimpleNamespace(
        column_names=["instrument_id", "symbol", "provider_symbol", "universe"],
        result_rows=[
            (removed, "OLD", "OLD.US", "previous"),
            (retained, "AAPL", "AAPL.US", "previous"),
        ],
    )
    storage = USStorage(client)
    storage.sync_expected_series([
        {"instrument_id": retained, "symbol": "AAPL", "provider_symbol": "AAPL.US"},
        {"instrument_id": added, "symbol": "MSFT", "provider_symbol": "MSFT.US"},
    ], source="eodhd", universe="us_configured", resolution="daily")
    inserted = client.insert.call_args.args[1]
    columns = client.insert.call_args.kwargs["column_names"]
    rows = [dict(zip(columns, values, strict=True)) for values in inserted]
    assert {(row["symbol"], row["active"]) for row in rows} == {
        ("OLD", False), ("AAPL", True), ("MSFT", True)}
