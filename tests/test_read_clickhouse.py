import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "read_clickhouse.py"
spec = importlib.util.spec_from_file_location("read_clickhouse", SCRIPT)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


@pytest.mark.parametrize("sql", [
    "DELETE FROM market", "SELECT 1; DROP TABLE market", "WITH x AS (DELETE FROM t) SELECT x",
    "SELECT 1 FORMAT CSV", "SELECT 1 SETTINGS readonly=0", "SELECT 1; -- trailing comment",
])
def test_rejects_unsafe_sql(sql):
    with pytest.raises(reader.ReaderError):
        reader.validate_sql(sql)


def test_accepts_literals_comments_and_one_terminal_semicolon():
    assert reader.validate_sql("WITH x AS (SELECT ';' AS s) SELECT s FROM x;").endswith("x")
    assert reader.validate_sql("SELECT 'DELETE', 1 -- harmless")


def test_credentials_are_required_and_environment_wins(monkeypatch):
    values = {
        "CLICKHOUSE_SSH_HOST": "vps.example", "CLICKHOUSE_SSH_USER": "ubuntu",
        "CLICKHOUSE_USERNAME": "current", "CLICKHOUSE_PASSWORD": "secret",
        "CLICKHOUSE_DATABASE": "factorlab",
    }
    with patch.object(reader, "dotenv_values", return_value=values):
        monkeypatch.setenv("CLICKHOUSE_USERNAME", "override")
        assert reader.config()["CLICKHOUSE_USERNAME"] == "override"
        monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
        values.pop("CLICKHOUSE_PASSWORD")
        with pytest.raises(reader.ReaderError, match="CLICKHOUSE_PASSWORD"):
            reader.config()


def test_tunnel_cleanup_on_failure(monkeypatch):
    class Process:
        stopped = False

        def poll(self):
            return None if not self.stopped else 0

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            return 0

    process = Process()
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return process

    monkeypatch.setattr(reader.subprocess, "Popen", popen)
    with patch.object(reader.socket, "create_connection"):
        with pytest.raises(RuntimeError):
            with reader.ssh_tunnel({
                "CLICKHOUSE_SSH_PORT": "22", "CLICKHOUSE_SSH_KEY_PATH": "key",
                "CLICKHOUSE_HOST": "127.0.0.1", "CLICKHOUSE_PORT": "8123",
                "CLICKHOUSE_SSH_USER": "ubuntu", "CLICKHOUSE_SSH_HOST": "vps.example",
            }):
                raise RuntimeError("query failed")
    assert process.stopped
    assert "StrictHostKeyChecking=yes" in commands[0]
    assert "BatchMode=yes" in commands[0]
    assert "PreferredAuthentications=publickey" in commands[0]


def test_http_query_has_readonly_limits_and_keeps_password_in_header(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return io.BytesIO(json.dumps({"meta": [{"name": "x"}], "data": [[1]]}).encode())

        def __exit__(self, *args):
            pass

    def open_request(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(reader.urllib.request, "urlopen", open_request)
    result = reader.query("SELECT 1", {
        "CLICKHOUSE_USERNAME": "current", "CLICKHOUSE_PASSWORD": "private",
    }, 12345)
    assert result["data"] == [[1]]
    request = captured["request"]
    assert "readonly=1" in request.full_url
    assert "max_execution_time=30" in request.full_url
    assert "max_result_rows=30" in request.full_url
    assert "private" not in request.full_url
    assert request.get_header("Authorization").startswith("Basic ")


def test_output_limits():
    result = {"meta": [{"name": "value"}], "data": [["x" * 600] for _ in range(60)]}
    output = reader.format_result(result)
    assert len(output.encode()) <= reader.MAX_OUTPUT_BYTES
    assert output.count("\n") < 30
    assert "output limit reached" in output
