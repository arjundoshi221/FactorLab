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
sudo install -d -m 0755 -o root -g root /var/lib/factorlab/docker-images
sudo install -d -m 0700 -o root -g root /etc/factorlab/identity

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -d /etc/factorlab/us-universe.yaml ]; then
    echo "/etc/factorlab/us-universe.yaml is a directory; expected a config file" >&2
    exit 1
fi
if [ ! -f /etc/factorlab/us-universe.yaml ]; then
    sudo install -D -m 0644 "$script_dir/../us-universe.yaml" /etc/factorlab/us-universe.yaml
fi
sudo install -D -m 0755 "$script_dir/collect-docker-images.py" /usr/local/libexec/factorlab-collect-docker-images.py
sudo install -m 0644 "$script_dir/../systemd/factorlab-docker-images.service" /etc/systemd/system/factorlab-docker-images.service
sudo install -m 0644 "$script_dir/../systemd/factorlab-docker-images.timer" /etc/systemd/system/factorlab-docker-images.timer
sudo systemctl daemon-reload
sudo systemctl enable --now factorlab-docker-images.timer
sudo systemctl start factorlab-docker-images.service

echo "FactorLab production directories are ready."
