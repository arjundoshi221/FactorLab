ARG BASE_IMAGE=factorlab:schema-v2-preview-20260921
FROM ${BASE_IMAGE}

COPY scripts/factlab_us_clickhouse.py /app/scripts/factlab_us_clickhouse.py
COPY scripts/factlab_us_universe.py /app/scripts/factlab_us_universe.py

COPY factorlab/api/us.py /app/src/factorlab/api/us.py
COPY factorlab/storage/us_clickhouse.py /app/src/factorlab/storage/us_clickhouse.py
COPY factorlab/sources/eodhd/client.py /app/src/factorlab/sources/eodhd/client.py
COPY factorlab/sources/eodhd/configured_universe.py /app/src/factorlab/sources/eodhd/configured_universe.py

COPY factorlab/api/us.py /usr/local/lib/python3.12/site-packages/factorlab/api/us.py
COPY factorlab/storage/us_clickhouse.py /usr/local/lib/python3.12/site-packages/factorlab/storage/us_clickhouse.py
COPY factorlab/sources/eodhd/client.py /usr/local/lib/python3.12/site-packages/factorlab/sources/eodhd/client.py
COPY factorlab/sources/eodhd/configured_universe.py /usr/local/lib/python3.12/site-packages/factorlab/sources/eodhd/configured_universe.py
