#!/usr/bin/env sh
set -eu

COMPOSE_DIR="${FACTORLAB_COMPOSE_DIR:-/opt/factorlab/deploy}"

printf '%s factorlab political ingestion starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cd "$COMPOSE_DIR"

/usr/bin/docker compose \
  --env-file production.env \
  -f compose.production.yml \
  --profile jobs \
  run --rm --no-deps -T ingest-political

printf '%s factorlab political ingestion completed\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
