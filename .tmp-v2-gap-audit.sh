#!/usr/bin/env bash
set -u

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

echo '--- wave 1 validation gap report ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py validate --through-wave 1
validation_status=$?
echo "validation_exit=$validation_status"

echo '--- resolver workload ---'
sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import sys

sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration

client = migration.create_client()
try:
    queries = {
        "legacy_countries": "SELECT count() FROM factorlab.ref_countries FINAL",
        "legacy_exchanges": "SELECT count() FROM factorlab.ref_exchanges FINAL",
        "legacy_instruments": "SELECT count() FROM factorlab.ref_instruments FINAL",
        "legacy_contracts": "SELECT count() FROM factorlab.ref_contracts FINAL",
        "legacy_legislators": "SELECT count() FROM factorlab.alt_political_legislators FINAL",
        "legacy_raw_archives": "SELECT count() FROM factorlab.raw_http_archive",
        "approved_crosswalks": (
            "SELECT count() FROM meta.migration_id_crosswalk FINAL "
            "WHERE approved_at IS NOT NULL"
        ),
        "approved_reference_enrichments": (
            "SELECT count() FROM meta.migration_reference_enrichment FINAL "
            "WHERE approved_at IS NOT NULL"
        ),
        "approved_political_enrichments": (
            "SELECT count() FROM meta.migration_political_trade_enrichment FINAL "
            "WHERE approved_at IS NOT NULL"
        ),
        "v2_reference_rows": (
            "SELECT sum(total_rows) FROM system.tables WHERE database IN ('ref','raw')"
        ),
    }
    for label, query in queries.items():
        value = client.query(query).result_rows[0][0]
        print(f"{label}={value or 0}")
finally:
    client.close()
PY

exit 0
