#!/usr/bin/env bash
set -u

DEPLOY_DIR="${FACTORLAB_DEPLOY_DIR:-/opt/factorlab/deploy}"
COMPOSE_FILE="$DEPLOY_DIR/compose.production.yml"
ENV_FILE="$DEPLOY_DIR/production.env"
IDENTITY_DIR="${FACTORLAB_IDENTITY_DIR:-/etc/factorlab/identity}"
CLIENT_ID_FILE="$IDENTITY_DIR/cloudflare-access-client-id"
CLIENT_SECRET_FILE="$IDENTITY_DIR/cloudflare-access-client-secret"

echo "== FactorLab Cloudflare secrets diagnostic =="
echo

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: Run with sudo: sudo bash $0" >&2
  exit 2
fi

failed=0
for path in "$COMPOSE_FILE" "$ENV_FILE"; do
  if [[ -s "$path" ]]; then
    echo "OK: $path exists"
  else
    echo "ERROR: $path is missing or empty" >&2
    failed=1
  fi
done

for path in "$CLIENT_ID_FILE" "$CLIENT_SECRET_FILE"; do
  if [[ -s "$path" ]]; then
    stat -c 'OK: %n owner=%U:%G mode=%a bytes=%s' "$path"
  else
    echo "ERROR: $path is missing or empty" >&2
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

secrets_url="$(
  sed -n 's/^CLOUDFLARE_SECRETS_URL=//p' "$ENV_FILE" | tail -n 1
)"
if [[ "$secrets_url" != https://* ]]; then
  echo "ERROR: CLOUDFLARE_SECRETS_URL is missing or not HTTPS" >&2
  exit 1
fi
echo "OK: runtime URL is $secrets_url"

agent_id="$(
  docker ps \
    --filter 'label=com.docker.compose.service=cloudflare-secrets-agent' \
    --format '{{.ID}}' | head -n 1
)"
if [[ -z "$agent_id" ]]; then
  echo "ERROR: cloudflare-secrets-agent is not running" >&2
  echo "Current containers:"
  docker ps -a --format 'table {{.Names}}\t{{.Status}}'
  exit 1
fi
echo "OK: secrets-agent container is $agent_id"
echo

echo "Testing one request without following redirects."
echo "No credential values or response body will be printed."
echo

docker exec -i "$agent_id" python - <<'PY'
import os
from pathlib import Path

import requests

identity = Path("/run/identity")
headers = {
    "Accept": "application/json",
    "CF-Access-Client-Id": (
        identity / "cloudflare-access-client-id"
    ).read_text(encoding="utf-8").strip(),
    "CF-Access-Client-Secret": (
        identity / "cloudflare-access-client-secret"
    ).read_text(encoding="utf-8").strip(),
}

try:
    response = requests.post(
        os.environ["CLOUDFLARE_SECRETS_URL"],
        headers=headers,
        timeout=(5, 20),
        allow_redirects=False,
    )
except requests.RequestException as exc:
    print(f"RESULT: network error: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

status = response.status_code
content_type = response.headers.get("content-type", "<missing>")
location = response.headers.get("location", "<none>")

print(f"RESULT: HTTP status: {status}")
print(f"RESULT: Content-Type: {content_type}")
print(f"RESULT: Redirect: {location}")
print(f"RESULT: Response bytes: {len(response.content)}")
print()

if status in (301, 302, 303, 307, 308):
    print("DIAGNOSIS: Cloudflare redirected the service token.")
    print("FIX: Add a Service Auth policy containing this service token")
    print("to the Access application protecting the Worker.")
elif status in (401, 403):
    print("DIAGNOSIS: Cloudflare Access rejected the service token.")
    print("FIX: Check the Client ID/Secret and the Service Auth policy.")
elif status == 404:
    print("DIAGNOSIS: The configured Worker route was not found.")
    print("FIX: Check CLOUDFLARE_SECRETS_URL and the Worker deployment.")
elif status == 200 and "application/json" in content_type.lower():
    print("DIAGNOSIS: Access and the Worker endpoint are working.")
    print("The agent should succeed after it is restarted or on its next poll.")
elif status == 200:
    print("DIAGNOSIS: HTTP 200 returned non-JSON content.")
    print("This is commonly a Cloudflare login page or wrong Worker route.")
else:
    print("DIAGNOSIS: Unexpected upstream response; inspect Cloudflare logs.")
PY
probe_status=$?

echo
echo "Recent agent logs:"
docker logs --tail 20 "$agent_id" 2>&1 || true

exit "$probe_status"
