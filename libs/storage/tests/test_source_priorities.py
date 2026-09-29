"""P7: bindings -> ref.source_priorities rows; order must be unambiguous (07 §10)."""

from __future__ import annotations

import pytest

from factorlab.ingest.bindings import BindingError, BindingsFile, load_bindings
from factorlab.ingest.engine import source_priority_rows
from factorlab.schema import migrate as migration
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.v2_us import V2USStorage
from factorlab.testkit.fake_clickhouse import FakeClickHouse

DAILY = {"dataset": "market.bars", "market": "USA", "resolution": "daily"}


def test_rows_cover_only_source_keyed_datasets_and_keep_shadow_sources_apart():
    file = BindingsFile.model_validate(
        {
            "version": 1,
            "bindings": [
                {**DAILY, "provider": "schwab", "role": "primary", "priority": 10},
                {**DAILY, "provider": "eodhd", "role": "secondary", "priority": 20},
                {**DAILY, "provider": "ibkr", "role": "shadow", "priority": 5},
                {"dataset": "ref.listings", "market": "USA", "provider": "eodhd"},
            ],
        }
    )
    rows = source_priority_rows(file.bindings)
    assert [(r["source"], r["priority"], r["role"]) for r in rows] == [
        ("schwab", 10, "primary"),
        ("eodhd", 20, "secondary"),
        ("ibkr:shadow", 5, "shadow"),
    ]
    assert {r["country_code"] for r in rows} == {"US"}

    db = FakeClickHouse()
    assert ClickHouseSinks(V2USStorage(db)).sync_source_priorities(rows) == 3
    active = {r["source"]: r["active"] for r in db.log["ref.source_priorities"]}
    assert active == {"schwab": True, "eodhd": True, "ibkr:shadow": False}


def test_readable_bindings_need_distinct_priorities(tmp_path):
    import yaml

    path = tmp_path / "b.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "bindings": [
                    {**DAILY, "provider": "schwab", "role": "primary", "priority": 10},
                    {**DAILY, "provider": "eodhd", "role": "secondary", "priority": 10},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BindingError):
        load_bindings(path)


def test_wave_10_is_discovered_after_the_applied_waves():
    names = [m.migration_id for m in migration.discover_migrations() if m.wave == 10]
    assert names == ["wave_10_schema_source_priorities", "wave_10_views_source_priorities"]
    schema = [m for m in migration.discover_migrations() if m.phase == "schema"]
    assert schema[-2:] == [m for m in schema if m.wave == 10]
