#!/usr/bin/env bash
set -euo pipefail
image='factorlab:schema-v2-preview-20260921'
runner='/tmp/migrate_clickhouse_v2.py'
common=(--rm --network factorlab-backend --volumes-from factorlab-api-1:ro --read-only
  --tmpfs /tmp:size=8m,mode=0700 -e CLICKHOUSE_HOST=clickhouse -e CLICKHOUSE_PORT=8123
  -e CLICKHOUSE_USERNAME=factorlab -e FACTORLAB_SECRETS_DIR=/run/secrets/app
  -v "$runner:/app/scripts/migrate_clickhouse_v2.py:ro")
sudo docker run -i "${common[@]}" "$image" python - <<'PY'
import sys
sys.path.insert(0, "/app/scripts")
import migrate_clickhouse_v2 as migration
c = migration.create_client()
try:
    queries = {
        "exchanges": "SELECT exchange_code, any(country_code), any(currency_code), any(timezone), count() FROM factorlab.ref_exchanges FINAL GROUP BY exchange_code ORDER BY exchange_code",
        "instrument_exchange": "SELECT exchange_code, country_code, currency_code, count() FROM factorlab.ref_instruments FINAL GROUP BY exchange_code, country_code, currency_code ORDER BY exchange_code",
        "instrument_shapes": "SELECT asset_class, instrument_type, count() FROM factorlab.ref_instruments FINAL GROUP BY asset_class, instrument_type ORDER BY asset_class, instrument_type",
        "isin_coverage": "SELECT countIf(isin IS NOT NULL AND isin != ''), count(), uniqExactIf(isin, isin IS NOT NULL AND isin != '') FROM factorlab.ref_instruments FINAL",
        "contract_shapes": "SELECT contract_type, segment, count(), countIf(expiry IS NULL), countIf(strike_price IS NULL) FROM factorlab.ref_contracts FINAL GROUP BY contract_type, segment ORDER BY contract_type, segment",
        "missing_underlyings": "SELECT count() FROM factorlab.ref_contracts c FINAL LEFT JOIN factorlab.ref_instruments i FINAL ON c.instrument_id=i.instrument_id WHERE i.instrument_id=toUUID('00000000-0000-0000-0000-000000000000')",
        "duplicate_isin_listing_max": "SELECT max(n) FROM (SELECT isin, count() n FROM factorlab.ref_instruments FINAL WHERE isin IS NOT NULL AND isin != '' GROUP BY isin)",
    }
    for label, query in queries.items():
        print(f"--- {label} ---")
        for row in c.query(query).result_rows:
            print(" | ".join(str(item) for item in row))
finally:
    c.close()
PY
