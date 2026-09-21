#!/usr/bin/env bash
set -euo pipefail

image='factorlab:schema-v2-preview-20260921'
runner='/tmp/migrate_clickhouse_v2.py'
common=(
    --rm
    --network factorlab-backend
    --volumes-from factorlab-api-1:ro
    --read-only
    --tmpfs /tmp:size=8m,mode=0700
    -e CLICKHOUSE_HOST=clickhouse
    -e CLICKHOUSE_PORT=8123
    -e CLICKHOUSE_USERNAME=factorlab
    -e FACTORLAB_SECRETS_DIR=/run/secrets/app
    -v "$runner:/app/scripts/migrate_clickhouse_v2.py:ro"
)

echo '--- apply wave 0 schema ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py apply --phase schema --through-wave 0 --yes

echo '--- validate wave 0 ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py validate --through-wave 0

echo '--- idempotency rerun ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py apply --phase schema --through-wave 0 --yes

echo '--- verify control plane ---'
sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import sys

sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration

client = migration.create_client()
try:
    expected_databases = {
        "ref", "market", "fundamentals", "alt", "book", "risk",
        "derived", "broker", "meta", "raw", "research",
    }
    databases = {row[0] for row in client.query("SELECT name FROM system.databases").result_rows}
    missing = expected_databases - databases
    meta_tables = {
        row[0]
        for row in client.query(
            "SELECT name FROM system.tables WHERE database = 'meta'"
        ).result_rows
    }
    expected_meta = {
        "schema_migrations", "migration_runs", "migration_id_crosswalk",
        "migration_reference_enrichment", "migration_political_trade_enrichment",
    }
    journal = client.query(
        "SELECT argMax(status, version), argMax(checksum, version) "
        "FROM meta.schema_migrations WHERE migration_id = 'wave_00_schema'"
    ).result_rows[0]
    journal_checksum = (
        journal[1].decode("utf-8").rstrip("\x00")
        if isinstance(journal[1], bytes)
        else str(journal[1])
    )
    legacy_tables = client.query(
        "SELECT count() FROM system.tables WHERE database = 'factorlab'"
    ).result_rows[0][0]
    lock_count = client.query(
        "SELECT count() FROM system.tables WHERE database = 'default' "
        "AND name = '_factorlab_v2_migration_lock'"
    ).result_rows[0][0]
    assert not missing, missing
    assert expected_meta <= meta_tables, expected_meta - meta_tables
    assert journal[0] == "succeeded", journal
    assert journal_checksum == "e21095af41334e42431d380b16dc5afbd2f947c5b41d5e3a3aafc4da85421ef3", journal
    assert legacy_tables == 19, legacy_tables
    assert lock_count == 0, lock_count
    print("v2_databases=11")
    print(f"meta_control_tables={len(expected_meta)}")
    print(f"legacy_tables_unchanged={legacy_tables}")
    print(f"journal_status={journal[0]}")
    print("migration_lock_released=true")
finally:
    client.close()
PY
