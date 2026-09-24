"""Read-only deployment acceptance checks. Never print API credentials."""
import json
import os
from datetime import UTC, datetime

import requests

from factorlab.core.secrets import get_secret

base = os.getenv("VERIFY_API_BASE", "http://127.0.0.1:8000")
key = get_secret("FACTORLAB_API_KEY")
assert key, "API credential unavailable"
headers = {"Authorization": f"Bearer {key}"}
for path in ["/health", "/us", "/india", "/hub/api/v1/overview", "/hub/api/v1/us/dashboard",
             "/api/v1/us/instruments", "/api/v1/us/sources/status", "/api/v1/us/ingestion/runs",
             "/api/v1/us/candles/daily?limit=2", "/api/v1/us/candles/1min?limit=2",
             "/api/v1/india/candles/1min?limit=1", "/api/v1/political/trades?limit=1"]:
    response = requests.get(base + path, headers=headers, timeout=45)
    print(json.dumps({"path": path, "status": response.status_code}), flush=True)
    assert response.ok, f"Acceptance failed: {path} HTTP {response.status_code}"
    if path == "/hub/api/v1/us/dashboard":
        dashboard = response.json()
        assert {item["source"] for item in dashboard["sources"]} == {"universe", "schwab"}
        print(json.dumps(dashboard), flush=True)
assert requests.get(base + "/api/v1/us/candles/daily", timeout=30).status_code == 401
page = requests.get(base + "/api/v1/us/instruments", headers=headers, timeout=30).json()
if page["items"]:
    instrument = page["items"][0]
    response = requests.get(base + f'/api/v1/us/instruments/{instrument["instrument_id"]}/days', headers=headers, timeout=30)
    assert response.ok
    print(json.dumps({"instrument": instrument["symbol"], "session_checks": len(response.json()["items"])}))
print("US deployment HTTP acceptance checks passed", flush=True)
