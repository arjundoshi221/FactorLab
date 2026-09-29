#!/usr/bin/env bash
# sudoers entry point for platform releases (compose base, host tools, host logging).
# The installed deployer performs the swap; the bundle's deployer takes over next time.
#   deploy-platform.sh <version> <platform-bundle.tgz>
set -Eeuo pipefail

deployer="$(cd -- "$(dirname -- "$0")/.." && pwd)/host/factorlab_deploy.py"
if (( $# != 2 )); then
    echo "usage: deploy-platform.sh <version> <platform-bundle.tgz>" >&2
    exit 2
fi
exec /usr/bin/python3 "$deployer" platform "$@"
