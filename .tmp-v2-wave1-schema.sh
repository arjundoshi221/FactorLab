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

echo '--- wave 1 schema plan ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py plan --phase schema --through-wave 1

echo '--- apply through wave 1 schema ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py apply --phase schema --through-wave 1 --yes

echo '--- idempotency rerun ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py apply --phase schema --through-wave 1 --yes

echo '--- verify wave 1 schema ---'
sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import sys

sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration

client = migration.create_client()
try:
    ref_objects = client.query(
        "SELECT count() FROM system.tables WHERE database = 'ref'"
    ).result_rows[0][0]
    raw_objects = client.query(
        "SELECT count() FROM system.tables WHERE database = 'raw'"
    ).result_rows[0][0]
    raw_contract = client.query(
        "SELECT engine, storage_policy FROM system.tables "
        "WHERE database = 'raw' AND name = 'archive'"
    ).result_rows[0]
    journal = {
        row[0]: row[1]
        for row in client.query(
            "SELECT migration_id, argMax(status, version) FROM meta.schema_migrations "
            "GROUP BY migration_id"
        ).result_rows
    }
    row_counts = client.query(
        "SELECT database, sum(total_rows) FROM system.tables "
        "WHERE database IN ('ref', 'raw') GROUP BY database ORDER BY database"
    ).result_rows
    legacy_tables = client.query(
        "SELECT count() FROM system.tables WHERE database = 'factorlab'"
    ).result_rows[0][0]
    assert ref_objects == 24, ref_objects
    assert raw_objects == 1, raw_objects
    assert raw_contract == ("MergeTree", "raw_archive"), raw_contract
    assert journal.get("wave_00_schema") == "succeeded", journal
    assert journal.get("wave_01_schema") == "succeeded", journal
    assert journal.get("wave_01_views") == "succeeded", journal
    assert all(count == 0 for _, count in row_counts), row_counts
    assert legacy_tables == 19, legacy_tables
    print(f"ref_objects={ref_objects}")
    print(f"raw_objects={raw_objects}")
    print(f"raw_engine={raw_contract[0]} raw_policy={raw_contract[1]}")
    print("target_rows=0")
    print(f"legacy_tables_unchanged={legacy_tables}")
    print("wave_1_schema_status=succeeded")
finally:
    client.close()
PY
