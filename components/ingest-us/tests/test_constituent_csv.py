from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from factorlab.components.ingest_us.constituent_csv import ConstituentCsvUniverseResolver
from factorlab.components.ingest_us.settings import ConstituentCsvIndex
from factorlab.components.ingest_us.universe import UniverseConfig
from factorlab.components.ingest_us.universe.models import ResolvedConstituent


def csv_indexes(cfg):
    mapping = cfg.providers.constituent_csv
    assert mapping is not None
    return mapping.indexes


def config(path, count, **overrides):
    mapping = {
        "path": path,
        "symbol_column": "Symbol",
        "expected_constituents": count,
        "as_of": datetime.now(UTC) - timedelta(hours=1),
        "source_url": "https://example.com/entitled-roster",
        **overrides,
    }
    return UniverseConfig.model_validate(
        {
            "version": 2,
            "name": "us_russell3000",
            "provider": "constituent_csv",
            "daily_source": "schwab",
            "refresh_interval_minutes": 1440,
            "indexes": [{"name": "russell3000", "minimum_constituents": count}],
            "providers": {"constituent_csv": {"indexes": {"russell3000": mapping}}},
        }
    )


def test_entire_3000_symbol_roster_is_validated_and_resolved(tmp_path):
    path = tmp_path / "roster.csv"
    symbols = [f"S{i}" for i in range(3000)]
    path.write_text("Symbol\n" + "\n".join(symbols), encoding="utf-8")
    storage = Mock()
    resolver = ConstituentCsvUniverseResolver(
        indexes=csv_indexes(config(path, 3000)),
        validator=Mock(),
        storage=storage,
    )
    resolver.validator = Mock()
    resolver.validator.validate.return_value = [
        ResolvedConstituent(
            symbol=symbol, name=symbol, exchange="XNAS", currency="USD", instrument_type="EQUITY"
        )
        for symbol in symbols
    ]
    result = resolver.resolve(config(path, 3000).request())
    assert len(result.constituents) == 3000
    resolver.validator.validate.assert_called_once_with(sorted(symbols))
    assert result.provenance["indexes"]["russell3000"]["as_of"]
    storage.archive_raw.assert_called_once()
    assert storage.archive_raw.call_args.args[0].transport == "file"


@pytest.mark.parametrize(
    "body,count,error",
    [
        ("Symbol\nAAPL\n", 2, "expected exactly 2"),
        ("Symbol\nBRK.B\nBRK/B\n", 2, "duplicate"),
        ("Symbol\nAAPL\n\nMSFT\n", 3, "expected exactly 3"),
        ("Symbol\nAAPL\n,\n", 2, "Unsupported US symbol"),
        ("Ticker\nAAPL\n", 1, "no symbol column"),
    ],
)
def test_incomplete_or_malformed_roster_never_reaches_validation(tmp_path, body, count, error):
    path = tmp_path / "roster.csv"
    path.write_text(body, encoding="utf-8")
    cfg = config(path, count)
    resolver = ConstituentCsvUniverseResolver(
        indexes=csv_indexes(cfg), validator=Mock(), storage=Mock()
    )
    resolver.validator = Mock()
    with pytest.raises(ValueError, match=error):
        resolver.resolve(cfg.request())
    resolver.validator.validate.assert_not_called()


@pytest.mark.parametrize(
    "as_of",
    [
        datetime.now(UTC) - timedelta(days=8),
        datetime.now(UTC) + timedelta(days=1),
        datetime(2026, 10, 1),
    ],
)
def test_stale_or_undated_roster_is_rejected(tmp_path, as_of):
    path = tmp_path / "roster.csv"
    path.write_text("Symbol\nAAPL\n", encoding="utf-8")
    cfg = config(path, 1, as_of=as_of)
    resolver = ConstituentCsvUniverseResolver(
        indexes=csv_indexes(cfg), validator=Mock(), storage=Mock()
    )
    with pytest.raises(ValueError, match=r"stale|timezone"):
        resolver.resolve(cfg.request())


def test_roster_configuration_rejects_unknown_fields(tmp_path):
    with pytest.raises(ValueError):
        ConstituentCsvIndex.model_validate(
            {
                "path": tmp_path,
                "expected_constituents": 1,
                "as_of": datetime.now(UTC),
                "source_url": "source",
                "unexpected": True,
            }
        )


def test_one_unvalidated_constituent_prevents_publication(tmp_path):
    from factorlab.components.ingest_us.universe_daemon import sync_once

    path = tmp_path / "roster.csv"
    path.write_text("Symbol\nAAPL\nMSFT\n", encoding="utf-8")
    cfg = config(path, 2)
    storage = Mock()
    resolver = ConstituentCsvUniverseResolver(
        indexes=csv_indexes(cfg), validator=Mock(), storage=storage
    )
    resolver.validator = Mock()
    resolver.validator.validate.return_value = [
        ResolvedConstituent(
            symbol="AAPL", name="Apple", exchange="XNAS", currency="USD", instrument_type="EQUITY"
        )
    ]
    with pytest.raises(ValueError, match="could not validate"):
        sync_once(cfg, storage, resolver, client=Mock())
    storage.upsert_resolved_constituents.assert_not_called()
    storage.sync_expected_series.assert_not_called()
    storage.deactivate_expected_series.assert_not_called()
    assert storage.finish_ingestion_run.call_args.kwargs["status"] == "failed"
