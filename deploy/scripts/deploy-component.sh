#!/usr/bin/env bash
# sudoers entry point for per-component releases; the logic is in host/factorlab_deploy.py.
#   deploy-component.sh <component> <version> <image@sha256:...> <bundle.tgz>
#   deploy-component.sh rollback <component> [<version>]
set -Eeuo pipefail

deployer="$(cd -- "$(dirname -- "$0")/.." && pwd)/host/factorlab_deploy.py"
usage() {
    echo "usage: deploy-component.sh <component> <version> <image@sha256:...> <bundle.tgz>" >&2
    echo "       deploy-component.sh rollback <component> [<version>]" >&2
    exit 2
}

if [[ ${1:-} == rollback ]]; then
    shift
    case $# in
        1) exec /usr/bin/python3 "$deployer" rollback "$1" ;;
        2) exec /usr/bin/python3 "$deployer" rollback "$1" --to "$2" ;;
        *) usage ;;
    esac
fi
(( $# == 4 )) || usage
exec /usr/bin/python3 "$deployer" deploy "$@"
