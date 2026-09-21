#!/usr/bin/env bash
set -Eeuo pipefail

live=/opt/factorlab/deploy
context=/tmp/factorlab-us-configured-20260922
image=factorlab:us-configured-20260922
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/opt/factorlab/releases/us-configured-$stamp
stopped=false
switched=false
had_config=false

compose() {
    docker compose --env-file "$live/production.env" -f "$live/compose.production.yml" "$@"
}

rollback() {
    local code=$?
    trap - ERR
    echo "US deployment failed; restoring $backup" >&2
    cp -f -- "$backup/compose.production.yml" "$live/compose.production.yml"
    cp -f -- "$backup/production.env" "$live/production.env"
    if [[ $had_config == true ]]; then
        cp -f -- "$backup/us-universe.yaml" /etc/factorlab/us-universe.yaml
    else
        rm -f -- /etc/factorlab/us-universe.yaml
    fi
    docker rm -f factorlab-universe-us-1 >/dev/null 2>&1 || true
    if [[ $stopped == true || $switched == true ]]; then
        compose up -d --no-deps --force-recreate ingest-us
    fi
    if [[ $switched == true ]]; then
        compose up -d --no-deps --force-recreate api
    fi
    exit "$code"
}
trap rollback ERR

mkdir -p -- "$backup"
cp -a -- "$live/compose.production.yml" "$backup/compose.production.yml"
cp -a -- "$live/production.env" "$backup/production.env"
if [[ -f /etc/factorlab/us-universe.yaml ]]; then
    had_config=true
    cp -a -- /etc/factorlab/us-universe.yaml "$backup/us-universe.yaml"
fi
docker inspect --format '{{.Id}} {{.State.StartedAt}}' factorlab-ingest-india-1 > "$backup/india-before.txt"
docker inspect --format '{{.Config.Image}} {{.Image}}' factorlab-api-1 factorlab-ingest-us-1 \
    > "$backup/us-images-before.txt"

install -m 0644 "$context/compose.production.yml" "$live/compose.production.yml"
install -D -m 0644 "$context/us-universe.yaml" /etc/factorlab/us-universe.yaml

env_tmp=$(mktemp "$live/production.env.XXXXXX")
awk '!/^FACTORLAB_(US|API)_IMAGE=/' "$live/production.env" > "$env_tmp"
printf 'FACTORLAB_US_IMAGE=%s\nFACTORLAB_API_IMAGE=%s\n' "$image" "$image" >> "$env_tmp"
chmod --reference="$live/production.env" "$env_tmp"
chown --reference="$live/production.env" "$env_tmp"
mv -f -- "$env_tmp" "$live/production.env"

compose config --quiet
compose stop ingest-us
stopped=true

compose run --rm --no-deps universe-us \
    python scripts/factlab_us_universe.py --config /app/config/us-universe.yaml --once

compose up -d --no-deps --force-recreate api ingest-us universe-us
switched=true

deadline=$((SECONDS + 120))
until curl --fail --silent --show-error --max-time 10 \
        http://127.0.0.1:8000/health >/dev/null; do
    (( SECONDS < deadline ))
    sleep 3
done

status=$(curl --fail --silent --show-error --max-time 15 \
    http://127.0.0.1:8000/hub/api/v1/us/sources/status)
grep -q '"source":"universe"' <<<"$status"

for service in api ingest-us universe-us; do
    container=$(compose ps -q "$service")
    [[ -n $container ]]
    [[ $(docker inspect --format '{{.State.Status}}' "$container") == running ]]
    [[ $(docker inspect --format '{{.Config.Image}}' "$container") == "$image" ]]
done

docker inspect --format '{{.Id}} {{.State.StartedAt}}' factorlab-ingest-india-1 > "$backup/india-after.txt"
cmp "$backup/india-before.txt" "$backup/india-after.txt"

trap - ERR
printf 'BACKUP=%s\n' "$backup"
printf 'STATUS=%s\n' "$status"
compose ps api ingest-us universe-us
