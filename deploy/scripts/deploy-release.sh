#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 4 )); then
    echo "usage: deploy-release.sh RELEASE_ID COMMIT IMAGE@DIGEST BUNDLE.tar.gz" >&2
    exit 2
fi

release_id=$1
commit=$2
image=$3
bundle=$4

[[ $release_id =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,12}$ ]] || { echo "invalid release id" >&2; exit 2; }
[[ $commit =~ ^[0-9a-f]{40}$ ]] || { echo "invalid commit" >&2; exit 2; }
[[ $image =~ ^ghcr\.io/arjundoshi221/factorlab@sha256:[0-9a-f]{64}$ ]] || { echo "invalid image digest" >&2; exit 2; }
[[ -f $bundle ]] || { echo "deployment bundle not found: $bundle" >&2; exit 2; }

root=/opt/factorlab
live=$root/deploy
record=$root/releases/$release_id
lock=/var/lock/factorlab-release.lock
stage=$(mktemp -d "$root/.release-stage.XXXXXX")
managed=(cloudflare-secrets-agent api ingest-india ingest-us)
rollback_state=not-required
verification_state=failed
previous_image=unknown
previous_release=unknown
deployment_started=false
writer_activated=false
first_v2_activation=false
if [[ ! -e $root/v2-cutover-activated ]]; then
    first_v2_activation=true
fi

cleanup() {
    rm -rf -- "$stage"
}
trap cleanup EXIT

exec 9>"$lock"
if ! flock -n 9; then
    echo "another FactorLab release is active" >&2
    exit 1
fi

compose() {
    docker compose --env-file "$live/production.env" -f "$live/compose.production.yml" "$@"
}

wait_healthy() {
    local service=$1 timeout=${2:-120} container status deadline
    container=$(compose ps -q "$service")
    [[ -n $container ]] || return 1
    deadline=$((SECONDS + timeout))
    while (( SECONDS < deadline )); do
        status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
        [[ $status == healthy ]] && return 0
        [[ $status == exited || $status == dead || $status == unhealthy ]] && return 1
        sleep 3
    done
    return 1
}

wait_http() {
    local path=$1 timeout=${2:-120} deadline
    deadline=$((SECONDS + timeout))
    while (( SECONDS < deadline )); do
        if curl --fail --silent --output /dev/null --max-time 30 \
            "http://127.0.0.1:8000$path"; then
            return 0
        fi
        sleep 2
    done
    return 1
}

service_exists() {
    compose config --services | grep -Fxq "$1"
}

running_with_image() {
    local service=$1 expected=$2 container actual state
    container=$(compose ps -q "$service")
    [[ -n $container ]] || return 1
    state=$(docker inspect --format '{{.State.Status}}' "$container")
    actual=$(docker inspect --format '{{.Config.Image}}' "$container")
    [[ $state == running && $actual == "$expected" ]]
}

write_image_pins() {
    local target=$1 value=$2 temporary
    temporary=$(mktemp "${target}.XXXXXX")
    awk '!/^(FACTORLAB_IMAGE|FACTORLAB_US_IMAGE|FACTORLAB_API_IMAGE)=/' "$target" > "$temporary"
    {
        printf 'FACTORLAB_IMAGE=%s\n' "$value"
        printf 'FACTORLAB_US_IMAGE=%s\n' "$value"
        printf 'FACTORLAB_API_IMAGE=%s\n' "$value"
    } >> "$temporary"
    chmod --reference="$target" "$temporary"
    chown --reference="$target" "$temporary"
    mv -f -- "$temporary" "$target"
}

verify_current() {
    local service response path
    wait_healthy cloudflare-secrets-agent 120
    for service in cloudflare-secrets-agent api ingest-india ingest-us; do
        running_with_image "$service" "$image"
    done
    if service_exists universe-us; then
        running_with_image universe-us "$image"
    fi
    for service in bootstrap ingest-political universe-us; do
        [[ $(compose --profile jobs config --format json | python3 -c \
            "import json,sys; data=json.load(sys.stdin); print(data['services'][sys.argv[1]]['image'])" \
            "$service") == "$image" ]]
    done
    wait_http /health
    response=$(curl --fail --silent --show-error --max-time 15 http://127.0.0.1:8000/health)
    grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' <<<"$response"
    for path in overview schema-map india/dashboard us/dashboard political/dashboard; do
        wait_http "/hub/api/v1/$path"
    done
    sleep "${FACTORLAB_STABILIZATION_SECONDS:-30}"
    for service in ingest-india ingest-us; do
        running_with_image "$service" "$image"
    done
    if service_exists universe-us; then
        running_with_image universe-us "$image"
    fi
}

rollback() {
    local service old_services old_image container
    set +e
    rollback_state=failed

    old_services=$(cat "$record/previous-services.txt")
    for service in "${managed[@]}" universe-us; do
        if ! grep -Fxq "$service" <<<"$old_services" && service_exists "$service"; then
            compose stop "$service"
            compose rm -f "$service"
        fi
    done

    rm -rf -- "$live"
    cp -a -- "$record/previous-deploy" "$live"

    if service_exists cloudflare-secrets-agent && grep -Fxq cloudflare-secrets-agent <<<"$old_services"; then
        compose up -d --no-deps --force-recreate cloudflare-secrets-agent
        wait_healthy cloudflare-secrets-agent 120 || return 1
        old_image=$(awk -F '\t' '$1 == "cloudflare-secrets-agent" { print $2 }' \
            "$record/previous-running-images.tsv")
        [[ -z $old_image ]] || running_with_image cloudflare-secrets-agent "$old_image" || return 1
    fi
    compose run --rm --no-deps bootstrap || return 1
    while IFS=$'\t' read -r service old_image; do
        [[ -n $service ]] || continue
        [[ $service == cloudflare-secrets-agent ]] && continue
        compose up -d --no-deps --force-recreate "$service" || return 1
        container=$(compose ps -q "$service")
        [[ -n $container ]] || return 1
        [[ $(docker inspect --format '{{.State.Status}}' "$container") == running ]] || return 1
        [[ $(docker inspect --format '{{.Config.Image}}' "$container") == "$old_image" ]] || return 1
    done < "$record/previous-running-images.tsv"
    wait_healthy cloudflare-secrets-agent 120 || return 1
    wait_http /health || return 1
    wait_http /hub/api/v1/overview || return 1
    sleep "${FACTORLAB_STABILIZATION_SECONDS:-30}"
    while IFS=$'\t' read -r service old_image; do
        [[ $service == ingest-india || $service == ingest-us || $service == universe-us ]] || continue
        container=$(compose ps -q "$service")
        [[ -n $container && $(docker inspect --format '{{.State.Status}}' "$container") == running ]] || return 1
    done < "$record/previous-running-images.tsv"
    if [[ $first_v2_activation == true ]]; then
        for service in ingest-india ingest-us; do
            if grep -Fxq "$service" <<<"$old_services"; then
                compose up -d --no-deps --force-recreate "$service" || return 1
            fi
        done
        bash "$live/scripts/install-political-cron.sh" || return 1
    fi
    rollback_state=succeeded
}

on_error() {
    local exit_code=$?
    trap - ERR
    if [[ $deployment_started == true ]]; then
        if [[ $writer_activated == true || -e $root/v2-cutover-activated ]]; then
            echo "release failed after v2 writer activation; stopping writers for a v2 fix-forward release" >&2
            compose stop ingest-india ingest-us universe-us 2>/dev/null || true
            if [[ $first_v2_activation == true ]]; then
                rm -f -- /etc/cron.d/factorlab-political
                systemctl reload-or-restart cron 2>/dev/null || true
            fi
            rollback_state=fix-forward-required
        else
            echo "release verification failed before v2 writer activation; restoring previous deployment" >&2
            rollback || true
        fi
    else
        rm -rf -- "$record"
    fi
    echo "FACTORLAB_RESULT_PREVIOUS_IMAGE=$previous_image"
    echo "FACTORLAB_RESULT_VERIFICATION=$verification_state"
    echo "FACTORLAB_RESULT_ROLLBACK=$rollback_state"
    exit "$exit_code"
}
trap on_error ERR

[[ -f $live/production.env && -f $live/compose.production.yml ]] || {
    echo "existing production deployment is incomplete" >&2
    exit 1
}
[[ ! -e $record ]] || { echo "release record already exists: $record" >&2; exit 1; }

if [[ $first_v2_activation == true ]]; then
    for service in ingest-india ingest-us universe-us; do
        if service_exists "$service"; then
            container=$(compose ps -q "$service")
            [[ -z $container ]] || {
                echo "first v2 activation requires paused legacy writer: $service" >&2
                exit 1
            }
        fi
    done
    [[ ! -e /etc/cron.d/factorlab-political ]] || {
        echo "first v2 activation requires paused political cron" >&2
        exit 1
    }
fi

mkdir -p -- "$record"
cp -a -- "$live" "$record/previous-deploy"
if [[ -f $root/current-release ]]; then
    previous_release=$(<"$root/current-release")
fi
compose config --services > "$record/previous-services.txt"
: > "$record/previous-running-images.tsv"
for service in "${managed[@]}" universe-us; do
    if service_exists "$service"; then
        container=$(compose ps -q "$service")
        if [[ -n $container && $(docker inspect --format '{{.State.Status}}' "$container") == running ]]; then
            old_image=$(docker inspect --format '{{.Config.Image}}' "$container")
            printf '%s\t%s\n' "$service" "$old_image" >> "$record/previous-running-images.tsv"
            [[ $service == api ]] && previous_image=$old_image
        fi
    fi
done
printf 'release_id=%s\ncommit=%s\nimage=%s\nprevious_release=%s\nprevious_image=%s\n' \
    "$release_id" "$commit" "$image" "$previous_release" "$previous_image" \
    > "$record/release.env"

tar -xzf "$bundle" -C "$stage"
[[ -f $stage/deploy/compose.production.yml && -f $stage/deploy/scripts/prepare-host.sh ]] || {
    echo "bundle is missing required deployment files" >&2
    exit 1
}
cp -- "$live/production.env" "$stage/deploy/production.env"
write_image_pins "$stage/deploy/production.env" "$image"
docker compose --env-file "$stage/deploy/production.env" \
    -f "$stage/deploy/compose.production.yml" config --quiet
cp -a -- "$stage/deploy" "$record/new-deploy"

docker pull "$image"
deployment_started=true
rm -rf -- "$live"
cp -a -- "$stage/deploy" "$live"
compose config --quiet

bash "$live/scripts/prepare-host.sh"
compose up -d --no-deps --force-recreate cloudflare-secrets-agent
wait_healthy cloudflare-secrets-agent 120
compose run --rm --no-deps bootstrap
compose up -d --no-deps --force-recreate api
running_with_image api "$image"
wait_http /health
for path in overview schema-map india/dashboard us/dashboard political/dashboard; do
    wait_http "/hub/api/v1/$path"
done
activation_time=$(date -u +'%Y-%m-%dT%H:%M:%S+00:00')
if [[ $first_v2_activation == true ]]; then
    printf '%s\n' "$release_id" > "$root/v2-cutover-activated"
fi
writer_activated=true
if service_exists universe-us; then
    compose up -d --no-deps --force-recreate universe-us
fi
compose up -d --no-deps --force-recreate ingest-india
compose up -d --no-deps --force-recreate ingest-us

verify_current
if [[ $first_v2_activation == true ]]; then
    compose --profile jobs run --rm --no-deps bootstrap \
        python scripts/verify_clickhouse_v2_writes.py \
        --since "$activation_time" --wait-seconds "${FACTORLAB_V2_WRITE_WAIT_SECONDS:-900}"
    bash "$live/scripts/install-political-cron.sh"
fi
verification_state=succeeded
printf '%s\n' "$image" > "$root/current-image"
date -u +'%Y-%m-%dT%H:%M:%SZ' > "$record/activated-at"
printf '%s\n' "$release_id" > "$root/current-release"

echo "FACTORLAB_RESULT_PREVIOUS_IMAGE=$previous_image"
echo "FACTORLAB_RESULT_VERIFICATION=$verification_state"
echo "FACTORLAB_RESULT_ROLLBACK=$rollback_state"
echo "FactorLab release $release_id deployed successfully."
