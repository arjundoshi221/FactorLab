#!/usr/bin/env sh
set -eu

if ! mountpoint -q /mnt/factorlab-data; then
    echo "/mnt/factorlab-data is not mounted; refusing to create production paths" >&2
    exit 1
fi

sudo install -d -m 0750 -o 101 -g 101 /var/lib/factorlab/clickhouse
sudo install -d -m 0750 -o 101 -g 101 /mnt/factorlab-data/clickhouse-raw
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/app-data
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/logs
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/uptime-kuma
sudo install -d -m 0750 -o 1000 -g 1000 /var/lib/factorlab/portainer
sudo install -d -m 0700 -o root -g root /etc/factorlab/identity

echo "FactorLab production directories are ready."
