from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from factorlab.components.ingest_us.universe import UniverseConfig
from factorlab.components.ingest_us.universe.base import UniverseResolver
from factorlab.components.ingest_us.universe.models import (
    IndexRequest,
    ResolvedConstituent,
    UniverseRequest,
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
            "github_csv": {
                "indexes": {
                    "sp500": {
                        "url": "https://raw.githubusercontent.com/example/repo/main/index.csv",
                        "symbol_column": "Symbol",
                    }
                }
            },
            "eodhd": {"indexes": {"sp500": {"symbol": "GSPC.INDX"}}},
        },
        "extra_symbols": ["BRK.B"],
    }
    value.update(changes)
    return value


def constituent(symbol):
    return ResolvedConstituent(
        symbol=symbol, name=symbol, exchange="XNAS", currency="USD", instrument_type="EQUITY"
    )


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
        UniverseConfig.model_validate(payload(providers={"eodhd": payload()["providers"]["eodhd"]}))


def test_base_resolver_normalizes_deduplicates_and_validates_complete_union():
    validator = Mock()
    validator.validate.return_value = [
        constituent("AAPL"),
        constituent("BRK-B"),
        constituent("MSFT"),
    ]
    resolver = Resolver({"first": ["AAPL", "BRK.B"], "second": ["aapl", "MSFT"]}, validator)
    result = resolver.resolve(
        UniverseRequest(
            name="test",
            indexes=(
                IndexRequest(name="first", minimum_constituents=2),
                IndexRequest(name="second", minimum_constituents=2),
            ),
            explicit_symbols=("BRK/B",),
        )
    )
    assert [item.symbol for item in result.constituents] == ["AAPL", "BRK-B", "MSFT"]
    validator.validate.assert_called_once_with(["AAPL", "BRK-B", "MSFT"])
    assert normalize_symbol("BRK.B") == normalize_symbol("BRK/B") == "BRK-B"


def test_normalize_symbol_keeps_index_dollar_prefix():
    assert normalize_symbol("$spx") == "$SPX"
    for bad in ("$", "$$SPX", "S$PX"):
        with pytest.raises(ValueError, match="Unsupported US symbol"):
            normalize_symbol(bad)


def test_base_resolver_rejects_minimum_and_partial_validation():
    validator = Mock()
    resolver = Resolver({"sp500": ["AAPL"]}, validator)
    request = UniverseRequest(
        name="test", indexes=(IndexRequest(name="sp500", minimum_constituents=2),)
    )
    with pytest.raises(ValueError, match="minimum is 2"):
        resolver.resolve(request)
    validator.validate.assert_not_called()

    validator.validate.return_value = []
    resolver.records["sp500"] = ["AAPL", "MSFT"]
    with pytest.raises(ValueError, match="could not validate"):
        resolver.resolve(request)


def ishares_payload(**changes):
    value = payload(
        provider="ishares",
        indexes=[{"name": "r3k_iwv", "minimum_constituents": 2}],
        providers={
            "ishares": {
                "indexes": {
                    "r3k_iwv": {
                        "url": "https://www.ishares.com/us/products/239714/x/latest-holdings.csv"
                    }
                }
            }
        },
        extra_symbols=["$SPX", "XLK"],
        max_unvalidated=1,
        sector_provider="ishares",
    )
    value.update(changes)
    return value


def test_ishares_config_is_the_only_one_allowed_to_drop_members():
    config = UniverseConfig.model_validate(ishares_payload())
    assert config.request().max_unvalidated == 1
    assert config.extra_symbols == ["$SPX", "XLK"]
    with pytest.raises(ValidationError, match="only to holdings proxies"):
        UniverseConfig.model_validate(payload(max_unvalidated=3))
    with pytest.raises(ValidationError, match="sector_provider ishares must map"):
        UniverseConfig.model_validate(payload(sector_provider="ishares"))


def test_proxy_drops_unvalidated_members_within_allowance_but_never_extras():
    validator = Mock()
    validator.validate.return_value = [constituent("AAPL"), constituent("MSFT")]
    resolver = Resolver({"r3k_iwv": ["AAPL", "MSFT", "UWMC"]}, validator)
    request = UniverseRequest(
        name="test",
        indexes=(IndexRequest(name="r3k_iwv", minimum_constituents=2),),
        max_unvalidated=1,
    )
    result = resolver.resolve(request)
    assert [item.symbol for item in result.constituents] == ["AAPL", "MSFT"]
    assert result.provenance["unvalidated"] == ["UWMC"]
    tight = request.model_copy(update={"max_unvalidated": 0})
    with pytest.raises(ValueError, match="could not validate 1"):
        resolver.resolve(tight)
    extra = request.model_copy(update={"explicit_symbols": ("$SPX",)})
    with pytest.raises(ValueError, match="could not validate"):
        resolver.resolve(extra)  # $SPX is unvalidated and explicit: always fatal
