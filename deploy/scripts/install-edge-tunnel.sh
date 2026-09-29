#!/usr/bin/env bash
# Publish the Hub through an outbound-only Cloudflare Tunnel guarded by Cloudflare Access.
# The origin keeps listening on 127.0.0.1:8000 only; nothing is opened on 80/443/8000.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

env_file=/etc/factorlab/identity/cloudflared.env
apt_key_url=https://pkg.cloudflare.com/cloudflare-public-v2.gpg
apt_keyring=/usr/share/keyrings/cloudflare-public-v2.gpg
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

if [[ ! -f "$env_file" ]] || ! grep -q '^TUNNEL_TOKEN=.\+' "$env_file"; then
  echo "$env_file must contain TUNNEL_TOKEN=<token from Zero Trust > Networks > Tunnels>." >&2
  exit 1
fi
chown root:root "$env_file"
chmod 0600 "$env_file"

if ! command -v cloudflared >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL "$apt_key_url" -o "$apt_keyring"
  echo "deb [signed-by=$apt_keyring] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update
  apt-get install -y cloudflared
fi

install -m 0644 "$script_dir/../systemd/factorlab-cloudflared.service" /etc/systemd/system/factorlab-cloudflared.service
systemctl daemon-reload
systemctl enable factorlab-cloudflared.service
systemctl restart factorlab-cloudflared.service

# The previous public reverse proxy let clients reach the origin without passing Access.
if systemctl is-enabled --quiet caddy 2>/dev/null || systemctl is-active --quiet caddy 2>/dev/null; then
  systemctl disable --now caddy
  echo "Disabled caddy; public 80/443 are no longer served from this host."
fi

systemctl is-active --quiet factorlab-cloudflared.service
echo "Cloudflare Tunnel is running. Confirm the Access application covers every public hostname."
