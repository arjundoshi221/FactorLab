#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

if ! mountpoint -q /mnt/factorlab-data; then
    echo "/mnt/factorlab-data is not mounted; refusing to create production paths" >&2
    exit 1
fi

sudo install -d -m 0750 -o 101 -g 101 /var/lib/factorlab/clickhouse
sudo install -d -m 0750 -o 101 -g 101 /mnt/factorlab-data/clickhouse-raw
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/app-data
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/uptime-kuma
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/portainer
sudo install -d -m 0755 -o root -g root /var/lib/factorlab/docker-images
sudo install -d -m 0700 -o root -g root /etc/factorlab/identity

# Component containers run as UID 10001 (factorlab); log files are group-readable by
# factorlab-logs (GID 10002), the only group the restricted log-reader account joins.
getent group factorlab-logs >/dev/null || sudo groupadd --system --gid 10002 factorlab-logs
getent group factorlab >/dev/null || sudo groupadd --system --gid 10001 factorlab
id factorlab >/dev/null 2>&1 || sudo useradd --system --uid 10001 --gid factorlab \
    --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin factorlab

# One directory per component (keep in step with components/*/component.yaml; tested).
FACTORLAB_COMPONENTS="api web secrets-agent schema-migrator ingest-india ingest-us ingest-political ingest-broker"
sudo install -d -m 0750 -o root -g factorlab-logs /var/log/factorlab
for component in $FACTORLAB_COMPONENTS; do
    sudo install -d -m 2750 -o factorlab -g factorlab-logs "/var/log/factorlab/$component"
done
# The political cron log used to grow forever under /var/lib/factorlab/logs.
legacy_log=/var/lib/factorlab/logs/political-cron.log
if [ -f "$legacy_log" ]; then
    sudo gzip -c "$legacy_log" | sudo tee /var/log/factorlab/ingest-political/cron.log-legacy.gz >/dev/null
    sudo rm -f "$legacy_log"
fi

# Rotation and retention: logrotate hourly (30 days, size-capped), journald 1 GB / 30 days.
sudo install -m 0644 "$script_dir/../logrotate/factorlab" /etc/logrotate.d/factorlab
sudo install -D -m 0644 "$script_dir/../systemd/logrotate.timer.d/10-factorlab-hourly.conf" \
    /etc/systemd/system/logrotate.timer.d/10-factorlab-hourly.conf
sudo install -D -m 0644 "$script_dir/../systemd/journald.conf.d/60-factorlab.conf" \
    /etc/systemd/journald.conf.d/60-factorlab.conf
sudo logrotate --debug /etc/logrotate.d/factorlab >/dev/null
sudo systemctl restart systemd-journald

if [ -d /etc/factorlab/us-universe.yaml ]; then
    echo "/etc/factorlab/us-universe.yaml is a directory; expected a config file" >&2
    exit 1
fi
if [ ! -f /etc/factorlab/us-universe.yaml ]; then
    sudo install -D -m 0644 "$script_dir/../us-universe.yaml" /etc/factorlab/us-universe.yaml
fi
# Operator entry point to the live compose model (base + fragments + pins + profiles).
sudo install -m 0755 "$script_dir/factorlab-compose" /usr/local/bin/factorlab-compose

# Restricted log-reader account (tools/read_logs.py): sshd runs only factorlab-log-reader
# for it, and its only group is factorlab-logs. The operator adds its public key to
# /etc/factorlab/log-reader/authorized_keys (root-owned), prefixed with
# restrict,command="/usr/local/bin/factorlab-log-reader".
getent passwd factorlab-logs >/dev/null || sudo useradd --system --uid 10002 --gid factorlab-logs --no-create-home --home-dir /var/log/factorlab --shell /bin/sh factorlab-logs
sudo install -m 0755 "$script_dir/../host/factorlab_log_reader.py" /usr/local/bin/factorlab-log-reader
sudo install -d -m 0755 -o root -g root /etc/factorlab/log-reader
[ -f /etc/factorlab/log-reader/authorized_keys ] || sudo install -m 0644 -o root -g root /dev/null /etc/factorlab/log-reader/authorized_keys
if [ -d /etc/ssh/sshd_config.d ]; then
    sshd_dropin=/etc/ssh/sshd_config.d/60-factorlab-logs.conf
    sudo install -m 0644 "$script_dir/../ssh/60-factorlab-logs.conf" "$sshd_dropin"
    # Never leave sshd with a configuration it rejects: that could lock out the operator.
    if ! sudo sshd -t; then
        sudo rm -f "$sshd_dropin"
        echo "sshd rejected $sshd_dropin; removed it (SSH is unchanged)" >&2
        exit 1
    fi
    sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
fi
sudo install -D -m 0755 "$script_dir/collect-docker-images.py" /usr/local/libexec/factorlab-collect-docker-images.py
sudo install -m 0644 "$script_dir/../systemd/factorlab-docker-images.service" /etc/systemd/system/factorlab-docker-images.service
sudo install -m 0644 "$script_dir/../systemd/factorlab-docker-images.timer" /etc/systemd/system/factorlab-docker-images.timer
sudo systemctl daemon-reload
sudo systemctl enable --now logrotate.timer
sudo systemctl enable --now factorlab-docker-images.timer
sudo systemctl start factorlab-docker-images.service

echo "FactorLab production directories are ready."
