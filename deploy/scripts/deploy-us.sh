#!/usr/bin/env bash
# Apply a reviewed US release without recreating India, political, or secret-agent services.
set -euo pipefail
release=/opt/factorlab/releases/us-schwab-v1-20260909
cd /opt/factorlab/deploy
backup="/opt/factorlab/deploy/rollback-us-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp compose.production.yml production.env "$backup/"
if [ -f /etc/factorlab/us-universe.yaml ]; then
    cp /etc/factorlab/us-universe.yaml "$backup/"
fi
sudo docker inspect --format '{{.Id}} {{.State.StartedAt}}' factorlab-ingest-india-1 > "$backup/india-before.txt"
sudo docker load -i "$release/factorlab-us-schwab-v1-20260909.tar"
sudo cp "$release/compose.production.yml" compose.production.yml
sudo install -D -m 0644 "$release/us-universe.yaml" /etc/factorlab/us-universe.yaml
sudo python3 - <<'PY'
from pathlib import Path
path = Path('production.env')
lines = [line for line in path.read_text().splitlines()
         if not line.startswith(('FACTORLAB_US_IMAGE=', 'FACTORLAB_API_IMAGE='))]
lines += ['FACTORLAB_US_IMAGE=factorlab:us-schwab-v1-20260909',
          'FACTORLAB_API_IMAGE=factorlab:us-schwab-v1-20260909']
path.write_text('\n'.join(lines) + '\n')
PY
compose=(sudo docker compose --env-file production.env -f compose.production.yml)
"${compose[@]}" config --quiet
"${compose[@]}" run --rm --no-deps ingest-us python -c '
from pathlib import Path
from factorlab.storage.us_clickhouse import USStorage
store = USStorage.from_environment()
for statement in Path("sql/clickhouse/004_us_collection.sql").read_text().split(";"):
    if statement.strip():
        store.client.command(statement)
print("US schema ready")
'
"${compose[@]}" run --rm --no-deps universe-us python scripts/factlab_us_universe.py \
    --config /app/config/us-universe.yaml --once
if "${compose[@]}" run --rm --no-deps ingest-us python scripts/factlab_us_clickhouse.py --backfill; then
    echo 'Initial US collection completed'
else
    echo 'Initial US collection has unresolved work; daemon will retry and report coverage'
fi
"${compose[@]}" up -d --no-deps universe-us ingest-us api
sudo docker inspect --format '{{.Id}} {{.State.StartedAt}}' factorlab-ingest-india-1 > "$backup/india-after.txt"
cmp "$backup/india-before.txt" "$backup/india-after.txt"
printf 'Rollback configuration: %s\n' "$backup"
"${compose[@]}" ps --all
