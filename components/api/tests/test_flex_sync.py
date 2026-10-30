import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app

URL = "/hub/api/v1/portfolio/flex/runs"


@pytest.fixture
def fake_runner(monkeypatch):
    replies = {("POST", "/runs"): (202, {"state": "running", "job_id": "j1"})}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, method: str) -> None:
            status, body = replies.get((method, self.path), (404, {"detail": "Not found"}))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply("GET")

        def do_POST(self):
            self._reply("POST")

        def log_message(self, format, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("IBKR_FLEX_RUNNER_URL", f"http://127.0.0.1:{httpd.server_address[1]}/")
    try:
        yield replies
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_start_and_latest_pass_runner_status_through(fake_runner):
    api = TestClient(app)
    response = api.post(URL)
    assert (response.status_code, response.json()["job_id"]) == (202, "j1")

    fake_runner[("POST", "/runs")] = (429, {"detail": "cooldown", "retry_after": 12})
    assert api.post(URL).status_code == 429

    fake_runner[("GET", "/runs/latest")] = (200, {"state": "success", "rows_written": 3})
    assert api.get(f"{URL}/latest").json() == {"state": "success", "rows_written": 3}


def test_cross_site_start_is_rejected(fake_runner):
    response = TestClient(app).post(URL, headers={"sec-fetch-site": "cross-site"})
    assert response.status_code == 403


def test_unreachable_runner_is_503(monkeypatch):
    monkeypatch.setenv("IBKR_FLEX_RUNNER_URL", "http://127.0.0.1:9")
    response = TestClient(app).get(f"{URL}/latest")
    assert response.status_code == 503
    assert "Flex runner unavailable" in response.json()["detail"]
