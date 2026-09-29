"""The API writes one structured access line per request."""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.core.logging import configure_logging


def test_each_request_logs_route_status_duration_and_ray_id():
    root = logging.getLogger()
    before = list(root.handlers)
    out = io.StringIO()
    configure_logging(component="api", service="api", stream=out, fmt="json")
    try:
        TestClient(app).get("/definitely-not-a-route", headers={"cf-ray": "8c1f-SIN"})
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
    [line] = [json.loads(x) for x in out.getvalue().splitlines()
              if json.loads(x)["logger"] == "factorlab.api.access"]
    assert line["status"] == 404 and line["method"] == "GET"
    assert line["route"] == "/definitely-not-a-route"
    assert line["request_id"] == "8c1f-SIN" and line["duration_ms"] >= 0
    assert (line["component"], line["service"]) == ("api", "api")
