from __future__ import annotations

import os
import re

import pytest
from scripts import generate_clickhouse_v2_schema as generator
from scripts import migrate_clickhouse_v2 as migration

EXPECTED_DATABASES = {
    "ref",
    "market",
    "fundamentals",
    "alt",
    "book",
    "risk",
    "derived",
    "broker",
    "meta",
    "raw",
    "research",
}

EXPECTED_VIEWS = {
    "ref.session_windows_effective",
    "research.bars",
    "research.political_trades",
    "research.universe_membership",
    "research.owned_listings",
}


def all_sql() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(migration.MIGRATION_DIR.glob("wave_*.sql"))
    )


def test_checked_in_generated_schema_matches_design():
    for wave, statements in generator.collect_tables().items():
        expected = generator.render_wave(wave, statements)
        actual = (migration.MIGRATION_DIR / f"wave_{wave:02d}_schema.sql").read_text(
            encoding="utf-8"
        )
        assert actual == expected


def test_every_namespace_and_required_view_is_declared():
    sql = all_sql()
    databases = set(re.findall(r"CREATE DATABASE IF NOT EXISTS (\w+)", sql))
    views = set(re.findall(r"CREATE VIEW IF NOT EXISTS ([\w.]+)", sql))
    assert databases == EXPECTED_DATABASES
    assert EXPECTED_VIEWS <= views


def test_deferred_objects_are_not_accidentally_runnable():
    import json

    sql = all_sql().lower()
    deferred = json.loads((migration.MIGRATION_DIR / "deferred.json").read_text(encoding="utf-8"))[
        "items"
    ]
    for item in deferred:
        if item["kind"] in {"table", "materialized_view"}:
            assert f"create table if not exists {item['name'].lower()}" not in sql
            assert f"create materialized view {item['name'].lower()}" not in sql


def test_backfills_never_mutate_or_generate_canonical_ids_in_legacy():
    for path in migration.MIGRATION_DIR.glob("wave_*backfill*.sql"):
        sql = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(ALTER|DROP|TRUNCATE|DELETE|UPDATE)\b", sql, re.IGNORECASE)
        assert "generateUUIDv4" not in sql
        assert "generateUUIDv7" not in sql
        for statement in migration.split_sql(sql):
            assert re.match(r"(?:--.*\n)*\s*INSERT INTO (ref|raw|market|meta|alt)\.", statement)


def test_wave_02_does_not_collapse_contract_keyed_bars():
    sql = (migration.MIGRATION_DIR / "wave_02_backfill.sql").read_text(encoding="utf-8")
    zero_contract_filter = (
        "c.contract_id = toUUID('00000000-0000-0000-0000-000000000000')"
    )
    assert sql.count(zero_contract_filter) == 2
    assert sql.count("x.source_hash = i.migration_source_hash") == 2
    assert "'extended'" not in sql
    assert "WHERE security_type = 'equity'" in sql

    completion = (migration.MIGRATION_DIR / "wave_04_backfill_data_completion.sql").read_text(
        encoding="utf-8"
    )
    assert "INSERT INTO market.futures_contract_bars" in completion
    assert "v2c.underlying_listing_id = l.listing_id" in completion
    assert "INSERT INTO meta.expected_series" in completion


def test_wave_03_maps_all_known_legacy_market_codes():
    sql = (migration.MIGRATION_DIR / "wave_03_backfill.sql").read_text(encoding="utf-8")
    assert "['USA', 'IND', 'ALT_POLITICAL'], ['US', 'IN', 'US']" in sql
    assert "CAST([], 'Array(UUID)')" in sql


def test_wave_04_requires_current_approved_political_ids():
    sql = (migration.MIGRATION_DIR / "wave_04_validate.sql").read_text(encoding="utf-8")
    assert "-- check: political source rows lack approved current-hash canonical IDs" in sql
    assert "x.source_hash = legacy.source_hash" in sql
    assert "x.approved_at IS NOT NULL" in sql


def test_wave_09_prepares_canonical_operational_keys_without_exchanging_tables():
    schema = (migration.MIGRATION_DIR / "wave_09_schema_application_cutover.sql").read_text(
        encoding="utf-8"
    )
    backfill = (migration.MIGRATION_DIR / "wave_09_backfill_application_cutover.sql").read_text(
        encoding="utf-8"
    )
    assert "ORDER BY (country_code, listing_id, contract_id, source, resolution)" in schema
    assert "ORDER BY (country_code, listing_id, source, resolution, trade_date)" in schema
    assert "ORDER BY (country_code, listing_id, source, resolution)" in schema
    assert "legacy_instrument_id UUID DEFAULT" in schema
    assert "source_hash Nullable(FixedString(64))" in schema
    assert "CREATE TABLE IF NOT EXISTS meta.hub_schema_layouts" in schema
    assert "EXCHANGE TABLES" not in schema + backfill
    assert "FROM meta.expected_series FINAL" in backfill


@pytest.fixture(scope="module")
def clickhouse_client():
    if os.getenv("CLICKHOUSE_V2_INTEGRATION") != "1":
        pytest.skip("set CLICKHOUSE_V2_INTEGRATION=1 against an applied disposable v2 server")
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "18123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )
    yield client
    client.close()


def test_applied_schema_contract(clickhouse_client):
    databases = {
        row[0]
        for row in clickhouse_client.query(
            "SELECT name FROM system.databases WHERE name IN "
            "('ref','market','fundamentals','alt','book','risk','derived','broker','meta','raw','research')"
        ).result_rows
    }
    assert databases == EXPECTED_DATABASES
    assert clickhouse_client.query(
        "SELECT engine, storage_policy FROM system.tables WHERE database='raw' AND name='archive'"
    ).result_rows == [("MergeTree", "raw_archive")]
    views = {
        f"{row[0]}.{row[1]}"
        for row in clickhouse_client.query(
            "SELECT database,name FROM system.tables WHERE engine='View'"
        ).result_rows
    }
    assert EXPECTED_VIEWS <= views


def test_roles_exist_and_are_unassigned(clickhouse_client):
    roles = {
        row[0]
        for row in clickhouse_client.query(
            "SELECT name FROM system.roles WHERE name LIKE 'factorlab_%'"
        ).result_rows
    }
    assert roles == {"factorlab_research", "factorlab_ingest", "factorlab_admin"}
    assert clickhouse_client.query(
        "SELECT count() FROM system.role_grants "
        "WHERE granted_role_name IN ('factorlab_research','factorlab_ingest','factorlab_admin')"
    ).result_rows == [(0,)]
