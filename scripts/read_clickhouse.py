"""Run one bounded, read-only ClickHouse query through a temporary SSH tunnel."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MAX_ROWS = 30
MAX_OUTPUT_BYTES = 12 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class ReaderError(Exception):
    """A safe error message suitable for terminal output."""


def config() -> dict[str, str]:
    values = dotenv_values(ROOT / ".env")

    def get(name: str) -> str:
        return (os.environ.get(name) or values.get(name) or "").strip()

    result = {name: get(name) for name in (
        "CLICKHOUSE_SSH_HOST", "CLICKHOUSE_SSH_USER", "CLICKHOUSE_SSH_PORT",
        "CLICKHOUSE_SSH_KEY_PATH", "CLICKHOUSE_HOST", "CLICKHOUSE_PORT",
        "CLICKHOUSE_USERNAME", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE",
    )}
    required = ("CLICKHOUSE_SSH_HOST", "CLICKHOUSE_SSH_USER", "CLICKHOUSE_USERNAME",
                "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE")
    missing = [name for name in required if not result[name]]
    if missing:
        raise ReaderError(f"Missing configuration: {', '.join(missing)}")
    result["CLICKHOUSE_SSH_PORT"] = result["CLICKHOUSE_SSH_PORT"] or "22"
    result["CLICKHOUSE_PORT"] = result["CLICKHOUSE_PORT"] or "8123"
    result["CLICKHOUSE_HOST"] = result["CLICKHOUSE_HOST"] or "127.0.0.1"
    if result["CLICKHOUSE_HOST"] not in ("127.0.0.1", "localhost", "::1"):
        raise ReaderError("CLICKHOUSE_HOST must be VPS loopback")
    for name in ("CLICKHOUSE_SSH_PORT", "CLICKHOUSE_PORT"):
        try:
            port = int(result[name])
        except ValueError as exc:
            raise ReaderError(f"Invalid {name}") from exc
        if not 1 <= port <= 65535:
            raise ReaderError(f"Invalid {name}")
    return result


def validate_sql(sql: str) -> str:
    """Conservatively accept a single SELECT/WITH statement.

    The server's readonly setting is the actual write barrier. This scan also
    keeps accidental multi-statement or format/settings overrides out of the CLI.
    """
    if not sql.strip() or len(sql.encode("utf-8")) > 64 * 1024:
        raise ReaderError("SQL must be nonempty and at most 64 KB")
    masked = []
    i = 0
    while i < len(sql):
        char = sql[i]
        if char in ("'", '"', "`"):
            quote = char
            masked.append(" ")
            i += 1
            while i < len(sql):
                if sql[i] == "\\":
                    i += 2
                elif sql[i] == quote:
                    if i + 1 < len(sql) and sql[i + 1] == quote:
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            else:
                raise ReaderError("Unterminated SQL string or identifier")
        elif sql.startswith("--", i) or sql.startswith("#", i):
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
            masked.append(" ")
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ReaderError("Unterminated SQL comment")
            i = end + 2
            masked.append(" ")
        else:
            masked.append(char)
            i += 1
    visible = "".join(masked).strip()
    if visible.endswith(";") and sql.rstrip().endswith(";"):
        visible = visible[:-1].strip()
        sql = sql.rstrip()[:-1].rstrip()
    if ";" in visible or not re.match(r"^(SELECT|WITH)\b", visible, re.I):
        raise ReaderError("Only one SELECT or WITH query is allowed")
    if re.match(r"^WITH\b", visible, re.I) and not re.search(r"\bSELECT\b", visible, re.I):
        raise ReaderError("WITH query must contain SELECT")
    forbidden = ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE", "TRUNCATE",
                 "OPTIMIZE", "SYSTEM", "GRANT", "REVOKE", "ATTACH", "DETACH", "RENAME",
                 "SET", "SETTINGS", "FORMAT", "INTO", "OUTFILE", "KILL", "EXPLAIN")
    if re.search(r"\b(?:" + "|".join(forbidden) + r")\b", visible, re.I):
        raise ReaderError("Query contains a disallowed SQL keyword")
    return sql


@contextmanager
def ssh_tunnel(settings: dict[str, str]):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        local_port = probe.getsockname()[1]
    command = ["ssh", "-N", "-T", "-o", "BatchMode=yes", "-o",
               "PreferredAuthentications=publickey", "-o", "StrictHostKeyChecking=yes",
               "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
               "-p", settings["CLICKHOUSE_SSH_PORT"]]
    if settings["CLICKHOUSE_SSH_KEY_PATH"]:
        command += ["-i", settings["CLICKHOUSE_SSH_KEY_PATH"], "-o", "IdentitiesOnly=yes"]
    command += ["-L", f"127.0.0.1:{local_port}:{settings['CLICKHOUSE_HOST']}:"
                f"{settings['CLICKHOUSE_PORT']}",
                f"{settings['CLICKHOUSE_SSH_USER']}@{settings['CLICKHOUSE_SSH_HOST']}"]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ReaderError("Could not start SSH") from exc
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ReaderError("SSH tunnel failed; check key and trusted host key")
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise ReaderError("SSH tunnel timed out")
        yield local_port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def query(sql: str, settings: dict[str, str], local_port: int) -> dict:
    parameters = urllib.parse.urlencode({
        "readonly": "1", "max_execution_time": "30", "max_result_rows": str(MAX_ROWS),
        "result_overflow_mode": "break", "max_result_bytes": "49152",
        "default_format": "JSONCompact",
    })
    auth = base64.b64encode(
        f"{settings['CLICKHOUSE_USERNAME']}:{settings['CLICKHOUSE_PASSWORD']}".encode()
    ).decode("ascii")
    request = urllib.request.Request(
        f"http://127.0.0.1:{local_port}/?{parameters}", data=sql.encode("utf-8"),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ReaderError("ClickHouse request failed") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ReaderError("ClickHouse response exceeded 64 KB")
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReaderError("Invalid ClickHouse response") from exc


def format_result(result: dict) -> str:
    try:
        columns = [str(item["name"]) for item in result["meta"]]
        rows = result["data"][:MAX_ROWS]
    except (KeyError, TypeError) as exc:
        raise ReaderError("Invalid ClickHouse result") from exc
    lines = [" | ".join(columns)]
    used = len(lines[0].encode("utf-8")) + 1
    if used > MAX_OUTPUT_BYTES:
        raise ReaderError("ClickHouse result header exceeds output limit")
    shown = 0
    for row in rows:
        line = " | ".join(str(value).replace("\n", "\\n").replace("\r", "\\r")
                          for value in row)
        size = len(line.encode("utf-8")) + 1
        if used + size > MAX_OUTPUT_BYTES:
            break
        lines.append(line)
        used += size
        shown += 1
    if shown < len(rows):
        footer = f"[{shown} rows shown; output limit reached]"
        while lines and used + len(footer.encode("utf-8")) + 1 > MAX_OUTPUT_BYTES:
            removed = lines.pop()
            used -= len(removed.encode("utf-8")) + 1
            shown -= 1
            footer = f"[{shown} rows shown; output limit reached]"
        lines.append(footer)
    # print() adds a trailing newline; keep the complete terminal output bounded.
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql", help="one SELECT or WITH query")
    source.add_argument("--sql-file", type=Path, help="UTF-8 file containing one query")
    args = parser.parse_args(argv)
    try:
        sql = args.sql if args.sql is not None else args.sql_file.read_text(encoding="utf-8")
        sql = validate_sql(sql)
        settings = config()
        with ssh_tunnel(settings) as port:
            print(format_result(query(sql, settings, port)))
    except (ReaderError, OSError, UnicodeError) as exc:
        message = str(exc) if isinstance(exc, ReaderError) else "Could not read SQL file"
        print(f"read_clickhouse: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
