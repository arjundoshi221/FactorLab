#!/usr/bin/env bash
set -u

mode=${1:-plan}
image='factorlab:schema-v2-preview-20260921'
runner='/tmp/migrate_clickhouse_v2.py'
resolver='/tmp/resolve_clickhouse_v2_candidates.py'
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
    -v "$resolver:/app/scripts/resolve_clickhouse_v2_candidates.py:ro"
)

run_resolver() {
    sudo docker run "${common[@]}" "$image" \
        python scripts/resolve_clickhouse_v2_candidates.py "$@"
}

if [[ $mode == plan ]]; then
    run_resolver plan
    run_resolver report
    exit 0
fi
if [[ $mode != stage ]]; then
    echo "usage: $0 [plan|stage]" >&2
    exit 2
fi

echo '--- stage unapproved candidates ---'
run_resolver stage --yes

echo '--- idempotency rerun ---'
run_resolver stage --yes

echo '--- candidate report ---'
run_resolver report

echo '--- verify candidates and no approvals ---'
sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import sys
sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration
c = migration.create_client()
try:
    scalar = lambda query: int(c.query(query).result_rows[0][0])
    counts = {
        "entities": scalar("SELECT count() FROM ref.entities FINAL"),
        "securities": scalar("SELECT count() FROM ref.securities FINAL"),
        "listings": scalar("SELECT count() FROM ref.listings FINAL"),
        "contracts": scalar("SELECT count() FROM ref.contracts FINAL"),
        "countries": scalar("SELECT count() FROM ref.countries FINAL"),
        "currencies": scalar("SELECT count() FROM ref.currencies FINAL"),
        "exchanges": scalar("SELECT count() FROM ref.exchanges FINAL"),
        "crosswalks": scalar("SELECT count() FROM meta.migration_id_crosswalk FINAL"),
        "approved_crosswalks": scalar(
            "SELECT count() FROM meta.migration_id_crosswalk FINAL WHERE approved_at IS NOT NULL"
        ),
        "reference_enrichments": scalar(
            "SELECT count() FROM meta.migration_reference_enrichment FINAL"
        ),
        "approved_reference_enrichments": scalar(
            "SELECT count() FROM meta.migration_reference_enrichment FINAL WHERE approved_at IS NOT NULL"
        ),
        "dangling_listing_security": scalar(
            "SELECT count() FROM ref.listings l FINAL LEFT JOIN ref.securities s FINAL "
            "ON l.security_id=s.security_id WHERE s.security_id=toUUID('00000000-0000-0000-0000-000000000000')"
        ),
        "dangling_security_entity": scalar(
            "SELECT count() FROM ref.securities s FINAL LEFT JOIN ref.entities e FINAL "
            "ON s.entity_id=e.entity_id WHERE e.entity_id=toUUID('00000000-0000-0000-0000-000000000000')"
        ),
        "dangling_contract_listing": scalar(
            "SELECT count() FROM ref.contracts c FINAL LEFT JOIN ref.listings l FINAL "
            "ON c.underlying_listing_id=l.listing_id WHERE l.listing_id=toUUID('00000000-0000-0000-0000-000000000000')"
        ),
        "legacy_tables": scalar("SELECT count() FROM system.tables WHERE database='factorlab'"),
    }
    legacy_instruments = scalar("SELECT count() FROM factorlab.ref_instruments FINAL")
    legacy_securities = scalar(
        "SELECT uniqExact(if(isin IS NOT NULL AND isin != '', concat('isin:', upper(isin)), "
        "concat('legacy:', instrument_key))) FROM factorlab.ref_instruments FINAL"
    )
    legacy_contracts = scalar("SELECT count() FROM factorlab.ref_contracts FINAL")
    legacy_legislators = scalar(
        "SELECT uniqExact(bioguide_id) FROM factorlab.alt_political_legislators FINAL"
    )
    expected = {
        "entities": legacy_securities + legacy_legislators,
        "securities": legacy_securities,
        "listings": legacy_instruments,
        "contracts": legacy_contracts,
        "countries": 2,
        "currencies": 2,
        "exchanges": 3,
        "crosswalks": legacy_instruments + legacy_contracts + legacy_legislators,
        "approved_crosswalks": 0,
        "reference_enrichments": 5,
        "approved_reference_enrichments": 0,
        "dangling_listing_security": 0,
        "dangling_security_entity": 0,
        "dangling_contract_listing": 0,
        "legacy_tables": 19,
    }
    assert counts == expected, {"actual": counts, "expected": expected}
    for name, value in counts.items():
        print(f"{name}={value}")
finally:
    c.close()
PY

echo '--- expected approval gate ---'
sudo docker run "${common[@]}" "$image" \
    python scripts/migrate_clickhouse_v2.py validate --through-wave 1
validation_status=$?
echo "validation_exit=$validation_status"
[[ $validation_status == 2 ]]
