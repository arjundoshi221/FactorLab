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

sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import json
import sys

sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration

client = migration.create_client()
try:
    tables = (
        "ref_countries", "ref_exchanges", "ref_instruments", "ref_contracts",
        "alt_political_legislators",
    )
    for table in tables:
        print(f"--- {table} columns ---")
        columns = client.query(
            "SELECT name, type FROM system.columns WHERE database = 'factorlab' "
            f"AND table = '{table}' ORDER BY position"
        ).result_rows
        print(" | ".join(f"{name}:{kind}" for name, kind in columns))
        result = client.query(f"SELECT * FROM factorlab.{table} FINAL LIMIT 2")
        for row in result.result_rows:
            payload = {
                name: (str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value)
                for name, value in zip(result.column_names, row, strict=True)
            }
            print(json.dumps(payload, sort_keys=True))

    print("--- identity cardinalities ---")
    checks = {
        "instrument_keys": "SELECT uniqExact(instrument_key) FROM factorlab.ref_instruments FINAL",
        "instrument_ids": "SELECT uniqExact(instrument_id) FROM factorlab.ref_instruments FINAL",
        "instrument_id_to_keys_max": (
            "SELECT max(n) FROM (SELECT instrument_id, uniqExact(instrument_key) n "
            "FROM factorlab.ref_instruments FINAL GROUP BY instrument_id)"
        ),
        "symbols_to_instruments_max": (
            "SELECT max(n) FROM (SELECT country_code, exchange_code, trading_symbol, uniqExact(instrument_id) n "
            "FROM factorlab.ref_instruments FINAL GROUP BY country_code, exchange_code, trading_symbol)"
        ),
        "contract_keys": "SELECT uniqExact(contract_key) FROM factorlab.ref_contracts FINAL",
        "contract_ids": "SELECT uniqExact(contract_id) FROM factorlab.ref_contracts FINAL",
        "contract_underlyings": "SELECT uniqExact(instrument_id) FROM factorlab.ref_contracts FINAL",
        "legislator_bioguides": (
            "SELECT uniqExact(bioguide_id) FROM factorlab.alt_political_legislators FINAL"
        ),
    }
    for label, query in checks.items():
        print(f"{label}={client.query(query).result_rows[0][0]}")
finally:
    client.close()
PY
