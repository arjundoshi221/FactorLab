#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_DIR="${FACTORLAB_DEPLOY_DIR:-/opt/factorlab/deploy}"
COMPOSE_FILE="$DEPLOY_DIR/compose.production.yml"
ENV_FILE="$DEPLOY_DIR/production.env"
CLICKHOUSE_CONTAINER="factorlab-clickhouse"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 2
fi

query() {
  docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client \
    --database factorlab --query "$1"
}

row_count="$(query 'SELECT count() FROM raw_http_archive')"
current_policy="$(
  query "SELECT storage_policy FROM system.tables WHERE database = 'factorlab' AND name = 'raw_http_archive'"
)"

echo "raw_http_archive rows: $row_count"
echo "current storage policy: $current_policy"

if [[ "$current_policy" == "raw_archive" ]]; then
  echo "No repair needed."
elif [[ "$row_count" != "0" ]]; then
  echo "Refusing to recreate a non-empty raw_http_archive table." >&2
  exit 1
else
  query 'DROP TABLE raw_http_archive'
  query "$(cat <<'SQL'
CREATE TABLE raw_http_archive (
    raw_id UUID,
    source LowCardinality(String),
    source_url String,
    fetch_key String,
    status_code UInt16,
    response_headers String,
    response_body String CODEC(ZSTD(6)),
    content_type LowCardinality(String),
    content_encoding LowCardinality(String),
    response_sha256 FixedString(64),
    fetched_at DateTime64(3, 'UTC'),
    as_of_time DateTime64(3, 'UTC'),
    metadata_json String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(fetched_at)
ORDER BY (source, fetched_at, raw_id)
SETTINGS storage_policy = 'raw_archive'
SQL
)"
  echo "Recreated empty raw_http_archive on raw_archive storage."
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
  up --force-recreate bootstrap

echo "Bootstrap completed. Starting API."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d api
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -a
