#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

source_caddyfile=${1:-}
if [[ -z "$source_caddyfile" || ! -f "$source_caddyfile" ]]; then
  echo "Usage: $0 /path/to/Caddyfile" >&2
  exit 1
fi

if ! command -v caddy >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y caddy
fi

staged_caddyfile=$(mktemp)
trap 'rm -f "$staged_caddyfile"' EXIT
cp "$source_caddyfile" "$staged_caddyfile"

caddy fmt --overwrite "$staged_caddyfile"
caddy validate --config "$staged_caddyfile" --adapter caddyfile
install -o root -g caddy -m 0640 "$staged_caddyfile" /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy
