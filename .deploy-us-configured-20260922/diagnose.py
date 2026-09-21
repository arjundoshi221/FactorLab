from pathlib import Path

import requests

from factorlab.sources.eodhd.client import EODHDClient
from factorlab.sources.eodhd.configured_universe import load_config, resolve_universe
from factorlab.storage.us_clickhouse import USStorage

config = load_config("/app/config/us-universe.yaml")
print(f"CONFIG_OK name={config.name} indexes={len(config.indexes)}")

storage = USStorage.from_environment()
result = storage.client.query(
    "SELECT source, status, detail, checked_at FROM us_source_status FINAL "
    "WHERE source = 'universe'"
)
print(f"CLICKHOUSE_OK universe_status={result.result_rows}")

client = EODHDClient()
print(f"KEY_METADATA present={bool(client.api_key)} length={len(client.api_key)} "
      f"is_demo={client.api_key == 'demo'}")
try:
    master, resolved = resolve_universe(config, client)
except requests.HTTPError as exc:
    status = exc.response.status_code if exc.response is not None else "unknown"
    print(f"RESOLVE_HTTP_ERROR status={status}")
    raise SystemExit(1)
except Exception as exc:
    print(f"RESOLVE_ERROR type={type(exc).__name__}")
    raise SystemExit(1)
else:
    print(f"RESOLVE_OK master={len(master)} configured={len(resolved)}")
