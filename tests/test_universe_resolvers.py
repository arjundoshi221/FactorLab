from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from factorlab.storage.us_clickhouse import USStorage
from factorlab.universe import (
    EodhdUniverseResolver,
    GithubCsvUniverseResolver,
    IndexRequest,
    ResolvedConstituent,
    ResolvedUniverse,
    SchwabEquityValidator,
    UniverseConfig,
    UniverseRequest,
    UniverseResolver,
    create_resolver,
    normalize_symbol,
)


def payload(**changes):
    value = {
        "version": 2,
        "name": "us_configured",
        "provider": "github_csv",
        "daily_source": "schwab",
        "refresh_interval_minutes": 1440,
        "indexes": [{"name": "sp500", "minimum_constituents": 2}],
        "providers": {
            "github_csv": {"indexes": {"sp500": {
                "url": "https://raw.githubusercontent.com/example/repo/main/index.csv",
                "symbol_column": "Symbol",
            }}},
            "eodhd": {"indexes": {"sp500": {"symbol": "GSPC.INDX"}}},
        },
        "extra_symbols": ["BRK.B"],
    }
    value.update(changes)
    return value


def constituent(symbol):
    return ResolvedConstituent(symbol=symbol, name=symbol, exchange="XNAS",
                               currency="USD", instrument_type="EQUITY")


class Resolver(UniverseResolver):
    provider = "test"

    def __init__(self, records, validator):
        super().__init__(validator=validator)
        self.records = records

    def _retrieve_index(self, name):
        return self.records[name]


def test_v2_config_selects_logical_provider_mapping():
    config = UniverseConfig.model_validate(payload())
    assert config.request().indexes[0].name == "sp500"
    assert config.extra_symbols == ["BRK-B"]
    switched = UniverseConfig.model_validate(payload(provider="eodhd"))
    assert switched.provider == "eodhd"
    with pytest.raises(ValidationError, match="unknown universe provider"):
        UniverseConfig.model_validate(payload(provider="missing"))
    with pytest.raises(ValidationError, match="provider mapping is missing"):
        UniverseConfig.model_validate(payload(
            providers={"eodhd": payload()["providers"]["eodhd"]}))


def test_factory_switches_adapters_without_worker_changes():
    storage = Mock()
    schwab = Mock()
    github = create_resolver(
        UniverseConfig.model_validate(payload()), storage,
        schwab_client=schwab, session=Mock())
    eodhd = create_resolver(
        UniverseConfig.model_validate(payload(provider="eodhd")), storage,
        schwab_client=schwab, eodhd_client=Mock())
    assert isinstance(github, GithubCsvUniverseResolver)
    assert isinstance(eodhd, EodhdUniverseResolver)


def test_base_resolver_normalizes_deduplicates_and_validates_complete_union():
    validator = Mock()
    validator.validate.return_value = [constituent("AAPL"), constituent("BRK-B"),
                                       constituent("MSFT")]
    resolver = Resolver({"first": ["AAPL", "BRK.B"], "second": ["aapl", "MSFT"]},
                        validator)
    result = resolver.resolve(UniverseRequest(
        name="test", indexes=(IndexRequest(name="first", minimum_constituents=2),
                              IndexRequest(name="second", minimum_constituents=2)),
        explicit_symbols=("BRK/B",),
    ))
    assert [item.symbol for item in result.constituents] == ["AAPL", "BRK-B", "MSFT"]
    validator.validate.assert_called_once_with(["AAPL", "BRK-B", "MSFT"])
    assert normalize_symbol("BRK.B") == normalize_symbol("BRK/B") == "BRK-B"


def test_base_resolver_rejects_minimum_and_partial_validation():
    validator = Mock()
    resolver = Resolver({"sp500": ["AAPL"]}, validator)
    request = UniverseRequest(name="test",
        indexes=(IndexRequest(name="sp500", minimum_constituents=2),))
    with pytest.raises(ValueError, match="minimum is 2"):
        resolver.resolve(request)
    validator.validate.assert_not_called()

    validator.validate.return_value = []
    resolver.records["sp500"] = ["AAPL", "MSFT"]
    with pytest.raises(ValueError, match="could not validate"):
        resolver.resolve(request)


def test_github_csv_archives_and_checks_schema_and_final_host():
    response = SimpleNamespace(
        url="https://raw.githubusercontent.com/example/repo/main/index.csv",
        content=b"Symbol,Name\nAAPL,Apple\nBRK.B,Berkshire\n", status_code=200,
        ok=True, headers={"Content-Type": "text/csv"},
    )
    session = Mock()
    session.get.return_value = response
    storage = Mock()
    storage.archive_http_response.return_value = "raw-1"
    validator = Mock()
    validator.validate.return_value = [constituent("AAPL"), constituent("BRK-B")]
    mapping = UniverseConfig.model_validate(payload()).providers.github_csv.indexes
    resolver = GithubCsvUniverseResolver(
        indexes=mapping, validator=validator, storage=storage, session=session)
    result = resolver.resolve(UniverseRequest(name="test",
        indexes=(IndexRequest(name="sp500", minimum_constituents=2),)))
    assert len(result.constituents) == 2
    storage.archive_http_response.assert_called_once()

    response.url = "https://evil.example/index.csv"
    with pytest.raises(ValueError, match="approved GitHub host"):
        resolver.resolve(UniverseRequest(name="test",
            indexes=(IndexRequest(name="sp500", minimum_constituents=2),)))


def test_schwab_validator_batches_and_rejects_non_equity_foreign_or_unresolved():
    client = Mock()
    client.get.return_value = ({
        "AAPL": {"symbol": "AAPL", "assetMainType": "EQUITY",
                 "description": "Apple Inc.",
                 "reference": {"exchangeName": "NASDAQ", "currency": "USD"}},
        "SPY": {"symbol": "SPY", "assetMainType": "ETF",
                "reference": {"exchangeName": "NYSE ARCA", "currency": "USD"}},
        "SHOP": {"symbol": "SHOP", "assetMainType": "EQUITY",
                 "reference": {"exchangeName": "NASDAQ", "currency": "CAD"}},
        "CBOE": {"symbol": "CBOE", "assetMainType": "EQUITY",
                 "reference": {"exchangeName": "CBOE", "exchange": "Z"}},
        "errors": {"invalidSymbols": ["BAD"]},
    }, "raw")
    result = SchwabEquityValidator(client).validate(["AAPL", "BAD", "CBOE", "SHOP", "SPY"])
    assert [(item.symbol, item.exchange) for item in result] == [
        ("AAPL", "XNAS"), ("CBOE", "BATS")]


def test_worker_publication_is_schwab_only_and_deactivates_legacy(monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "neutral_us_universe_runner",
        Path(__file__).parents[1] / "scripts/factlab_us_universe.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    config = UniverseConfig.model_validate(payload())
    resolver = Mock()
    resolver.resolve.return_value = ResolvedUniverse(
        provider="github_csv", name=config.name, constituents=(constituent("AAPL"),),
        resolved_at=datetime.now(UTC), provenance={"raw_id": "raw-1"})
    storage = Mock()
    storage.upsert_resolved_constituents.return_value = {"AAPL": "id-aapl"}
    series = runner.sync_once(config, storage, resolver)
    assert series[0]["provider_symbol"] == "AAPL"
    assert storage.sync_expected_series.call_args.kwargs == {
        "source": "schwab", "universe": "us_configured", "resolution": "daily"}
    storage.deactivate_expected_series.assert_called_once_with(
        source="eodhd", resolution="daily")


def test_resolved_reference_upsert_does_not_deactivate_unrelated_instruments():
    client = Mock()
    client.query.return_value = SimpleNamespace(
        column_names=["instrument_key", "first_seen"], result_rows=[])
    storage = USStorage(client)
    storage.upsert_resolved_constituents([constituent("AAPL")])
    reference_call = next(
        call for call in client.insert.call_args_list if call.args[0] == "ref_instruments")
    columns = reference_call.kwargs["column_names"]
    records = [dict(zip(columns, row, strict=True)) for row in reference_call.args[1]]
    assert [(item["trading_symbol"], item["status"]) for item in records] == [
        ("AAPL", "active")]
