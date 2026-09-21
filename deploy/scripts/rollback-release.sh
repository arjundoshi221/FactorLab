#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )) || [[ ! $1 =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,12}$ ]]; then
    echo "usage: rollback-release.sh RELEASE_ID" >&2
    exit 2
fi

release_id=$1
root=/opt/factorlab
live=$root/deploy
record=$root/releases/$release_id
lock=/var/lock/factorlab-release.lock

[[ -f $record/previous-deploy/compose.production.yml ]] || {
    echo "no rollback bundle found for $release_id" >&2
    exit 1
}
[[ -f $record/previous-running-images.tsv && -f $record/previous-services.txt ]] || {
    echo "rollback metadata is incomplete for $release_id" >&2
    exit 1
}

exec 9>"$lock"
flock -n 9 || { echo "another FactorLab release operation is active" >&2; exit 1; }

compose() {
    docker compose --env-file "$live/production.env" -f "$live/compose.production.yml" "$@"
}

current_services=$(compose config --services)
previous_services=$(cat "$record/previous-services.txt")
for service in cloudflare-secrets-agent api ingest-india ingest-us universe-us; do
    if grep -Fxq "$service" <<<"$current_services" && ! grep -Fxq "$service" <<<"$previous_services"; then
        compose stop "$service"
        compose rm -f "$service"
    fi
done

rm -rf -- "$live"
cp -a -- "$record/previous-deploy" "$live"
compose config --quiet

compose up -d --no-deps --force-recreate cloudflare-secrets-agent
agent=$(compose ps -q cloudflare-secrets-agent)
deadline=$((SECONDS + 120))
while (( SECONDS < deadline )); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$agent")
    [[ $status == healthy ]] && break
    [[ $status == unhealthy || $status == exited || $status == dead ]] && exit 1
    sleep 3
done
[[ ${status:-unknown} == healthy ]] || { echo "secret agent did not become healthy" >&2; exit 1; }
expected_agent_image=$(awk -F '\t' '$1 == "cloudflare-secrets-agent" { print $2 }' \
    "$record/previous-running-images.tsv")
[[ -z $expected_agent_image ]] || \
    [[ $(docker inspect --format '{{.Config.Image}}' "$agent") == "$expected_agent_image" ]]

compose run --rm --no-deps bootstrap
while IFS=$'\t' read -r service expected_image; do
    [[ -n $service && $service != cloudflare-secrets-agent ]] || continue
    compose up -d --no-deps --force-recreate "$service"
    container=$(compose ps -q "$service")
    [[ $(docker inspect --format '{{.State.Status}}' "$container") == running ]]
    [[ $(docker inspect --format '{{.Config.Image}}' "$container") == "$expected_image" ]]
done < "$record/previous-running-images.tsv"

curl --fail --silent --show-error --output /dev/null --max-time 15 http://127.0.0.1:8000/health
curl --fail --silent --show-error --output /dev/null --max-time 30 \
    http://127.0.0.1:8000/hub/api/v1/overview
sleep "${FACTORLAB_STABILIZATION_SECONDS:-30}"
while IFS=$'\t' read -r service expected_image; do
    [[ $service == ingest-india || $service == ingest-us || $service == universe-us ]] || continue
    container=$(compose ps -q "$service")
    [[ -n $container && $(docker inspect --format '{{.State.Status}}' "$container") == running ]]
done < "$record/previous-running-images.tsv"

previous_release=$(sed -n 's/^previous_release=//p' "$record/release.env")
previous_image=$(sed -n 's/^previous_image=//p' "$record/release.env")
printf '%s\n' "${previous_release:-unknown}" > "$root/current-release"
printf '%s\n' "${previous_image:-unknown}" > "$root/current-image"
printf '%s\n' "Rolled back release $release_id successfully."
