#!/usr/bin/env sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root (for example, sudo $0)." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CRON_SOURCE="$SCRIPT_DIR/../cron/factorlab-political"
CRON_TARGET=/etc/cron.d/factorlab-political
LOG_DIR=/var/lib/factorlab/logs
LOCK_FILE=/run/lock/factorlab-political.lock
HOST_TIMEZONE=$(timedatectl show --property=Timezone --value)

case "$HOST_TIMEZONE" in
  UTC|Etc/UTC) ;;
  *)
    echo "Refusing to install: the cron expression assumes UTC, but the host timezone is $HOST_TIMEZONE." >&2
    exit 1
    ;;
esac

test -r "$CRON_SOURCE"
test -x /usr/bin/docker
test -x /usr/bin/flock
/usr/bin/docker compose version >/dev/null

chmod 0755 "$SCRIPT_DIR/run-political-ingest.sh"
install -d -m 0755 "$LOG_DIR"

if [ -L "$LOCK_FILE" ]; then
  echo "Refusing to install: $LOCK_FILE must not be a symbolic link." >&2
  exit 1
fi
if [ -e "$LOCK_FILE" ]; then
  test -f "$LOCK_FILE"
  chown root:root "$LOCK_FILE"
  chmod 0644 "$LOCK_FILE"
else
  install -o root -g root -m 0644 /dev/null "$LOCK_FILE"
fi

install -m 0644 "$CRON_SOURCE" "$CRON_TARGET"
systemctl enable --now cron >/dev/null
systemctl reload-or-restart cron

echo "Installed $CRON_TARGET"
echo "Schedule: daily at 02:15 UTC"
echo "Log: $LOG_DIR/political-cron.log"
