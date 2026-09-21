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

echo '--- wave 0 plan ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py plan --phase schema --through-wave 0

echo '--- migration status ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py status

echo '--- production prerequisites ---'
sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import sys

sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration

client = migration.create_client()
try:
    version = client.query("SELECT version()").result_rows[0][0]
    raw_policy = client.query(
        "SELECT count() FROM system.storage_policies WHERE policy_name = 'raw_archive'"
    ).result_rows[0][0]
    v2_tables = client.query(
        "SELECT count() FROM system.tables WHERE database IN "
        "('ref','market','fundamentals','alt','book','risk','derived','broker','meta','raw','research')"
    ).result_rows[0][0]
    legacy = client.query(
        "SELECT count(), sum(total_rows), sum(total_bytes) FROM system.tables "
        "WHERE database = 'factorlab'"
    ).result_rows[0]
    disks = client.query(
        "SELECT name, path, formatReadableSize(free_space), formatReadableSize(total_space) "
        "FROM system.disks ORDER BY name"
    ).result_rows
    grants = [row[0] for row in client.query("SHOW GRANTS").result_rows]
    print(f"clickhouse_version={version}")
    print(f"raw_archive_policy={bool(raw_policy)}")
    print(f"v2_live_tables={v2_tables}")
    print(f"legacy_tables={legacy[0]} legacy_rows={legacy[1]} legacy_bytes={legacy[2]}")
    for name, path, free, total in disks:
        print(f"disk={name} path={path} free={free} total={total}")
    print("grants=" + " | ".join(grants))
finally:
    client.close()
PY
