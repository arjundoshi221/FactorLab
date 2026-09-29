#!/usr/bin/env sh
set -eu

COMPOSE_DIR="${FACTORLAB_COMPOSE_DIR:-/opt/factorlab/deploy}"
PINS=/opt/factorlab/state/images.env

printf '%s factorlab political ingestion starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cd "$COMPOSE_DIR"

if [ -f "$PINS" ] && [ -x /usr/local/bin/factorlab-compose ]; then
  # Per-component releases: the deployer's model (base + fragments + image pins), so the
  # job runs the released ingest-political image.
  /usr/local/bin/factorlab-compose run --rm --no-deps -T ingest-political
else
  # Legacy monolith release path (until the platform bootstrap).
  /usr/bin/docker compose \
    --env-file production.env \
    -f compose.production.yml \
    --profile jobs \
    run --rm --no-deps -T ingest-political
fi

printf '%s factorlab political ingestion completed\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
