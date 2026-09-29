"""Read FactorLab production logs through the restricted ``factorlab-logs`` SSH account.

    uv run python tools/read_logs.py list
    uv run python tools/read_logs.py tail --component ingest-us [--service universe-us]
        [--since 2h] [--until ISO] [--level WARNING] [--grep TEXT] [--regex RE]
        [--run-id ID] [--lines 50] [--exc]
    uv run python tools/read_logs.py errors [--component api] [--since 24h]
    uv run python tools/read_logs.py run --run-id <meta.ingestion_runs.run_id> [--since 7d]

Add ``--json`` for raw JSON Lines. Add ``--local DIR`` to read a directory laid out like
/var/log/factorlab (fixtures, or a copy) without SSH.

The VPS runs deploy/host/factorlab_log_reader.py as sshd's ForceCommand for that
account, so this client can only ask for these reads. The windows, line counts and
redaction limits are enforced there. This client redacts again and caps what it
prints at 12 KiB (``--max-bytes``, at most 64 KiB).

Configuration comes from the environment or .env:
  FACTORLAB_LOGS_SSH_HOST      default: CLICKHOUSE_SSH_HOST
  FACTORLAB_LOGS_SSH_PORT      default: CLICKHOUSE_SSH_PORT, then 22
  FACTORLAB_LOGS_SSH_USER      default: factorlab-logs
  FACTORLAB_LOGS_SSH_KEY_PATH  optional (otherwise the SSH agent / default keys)
The VPS host key must already be in known_hosts: host-key checking is never relaxed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT, MAX_OUTPUT = 12 * 1024, 64 * 1024
MAX_RESPONSE = 256 * 1024
STANDARD = {"ts", "level", "component", "service", "logger", "msg", "version", "commit",
            "exc", "exc_summary", "raw"}


def _load_reader():
    key = "factorlab_log_reader"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, REPO / "deploy" / "host" / "factorlab_log_reader.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


reader = _load_reader()


class ClientError(Exception):
    """A safe message for the terminal."""


def settings() -> dict[str, str]:
    values = dotenv_values(REPO / ".env")

    def get(*names: str) -> str:
        for name in names:
            value = (os.environ.get(name) or values.get(name) or "").strip()
            if value:
                return value
        return ""

    result = {"host": get("FACTORLAB_LOGS_SSH_HOST", "CLICKHOUSE_SSH_HOST"),
              "port": get("FACTORLAB_LOGS_SSH_PORT", "CLICKHOUSE_SSH_PORT") or "22",
              "user": get("FACTORLAB_LOGS_SSH_USER") or "factorlab-logs",
              "key": get("FACTORLAB_LOGS_SSH_KEY_PATH")}
    if not result["host"]:
        raise ClientError("set FACTORLAB_LOGS_SSH_HOST (or CLICKHOUSE_SSH_HOST)")
    if not result["port"].isdigit() or not 1 <= int(result["port"]) <= 65535:
        raise ClientError("invalid FACTORLAB_LOGS_SSH_PORT")
    return result


def ssh_command(config: dict[str, str], argv: list[str]) -> list[str]:
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "PreferredAuthentications=publickey", "-o", "ConnectTimeout=10",
               "-o", "RequestTTY=no", "-o", "ClearAllForwardings=yes", "-p", config["port"]]
    if config["key"]:
        command += ["-i", config["key"], "-o", "IdentitiesOnly=yes"]
    # The remote side splits this with shlex and never passes it to a shell.
    return [*command, f"{config['user']}@{config['host']}", shlex.join(argv)]


def fetch_remote(argv: list[str]) -> str:
    try:
        result = subprocess.run(ssh_command(settings(), argv), capture_output=True,
                                timeout=90, check=False)
    except OSError as exc:
        raise ClientError("could not start ssh") from exc
    except subprocess.TimeoutExpired:
        raise ClientError("the log read timed out; narrow --since or add --service") from None
    stderr = result.stderr.decode("utf-8", "replace").strip()
    if result.returncode == 2 and stderr.startswith(("error:", "usage:")):
        raise ClientError(reader.redact(stderr.splitlines()[-1]))
    if result.returncode:
        raise ClientError("ssh failed (key, known_hosts or the factorlab-logs account?): "
                          + reader.redact(stderr[-300:]))
    return result.stdout[:MAX_RESPONSE].decode("utf-8", "replace")


def fetch_local(argv: list[str], root: Path) -> str:
    try:
        items, meta = reader.execute(argv, root=root, snapshots=())
    except reader.UsageError as exc:
        raise ClientError(str(exc)) from None
    lines = [json.dumps(item, ensure_ascii=False, default=str) for item in items]
    return "\n".join([*lines, json.dumps({"_meta": {**meta, "returned": len(items),
                                                      "truncated": 0}})]) + "\n"


# ── formatting ───────────────────────────────────────────────────────────────

def _extras(record: dict) -> str:
    pairs = [f"{k}={v}" for k, v in record.items() if k not in STANDARD and v not in (None, "")]
    text = " ".join(pairs)
    return f"  [{text[:300]}]" if text else ""


def format_record(record: dict) -> list[str]:
    where = f"{record.get('component', '?')}/{record.get('service', '?')}"
    head = " ".join(str(part) for part in (record.get("ts", "-"), record.get("level", "-"),
                                            where, record.get("logger", "")) if part)
    lines = [f"{head}: {record.get('msg', '')}{_extras(record)}"]
    if record.get("exc"):
        lines += ["    " + line for line in str(record["exc"]).splitlines()]
    elif record.get("exc_summary"):
        lines.append(f"    -> {record['exc_summary']}")
    return lines


def format_group(group: dict) -> list[str]:
    where = f"{group.get('component')}/{group.get('service')}"
    title = f"{group.get('count', 0):>5}x {where} {group.get('logger') or ''}: "
    seen = f"       last {group.get('last')}  first {group.get('first')}"
    if group.get("run_id"):
        seen += f"  run_id={group['run_id']}"
    lines = [title + str(group.get("fingerprint")), seen]
    if group.get("exc_summary"):
        lines.append(f"       -> {group['exc_summary']}")
    return lines


def format_listing(item: dict) -> list[str]:
    if "containers" in item:
        lines = [f"containers (snapshot {item.get('snapshot_at')}):"]
        lines += [f"  {c.get('service')!s:24} {c.get('status')!s:10} {c.get('version') or '-'}"
                  for c in item["containers"]]
        return lines
    files = ", ".join(f"{f['file']} ({f['bytes']} B, {f['modified'][:16]})"
                      for f in item.get("files", []))
    return [f"{item['component']}: {files or '(no log files)'}"]


def scrub(value):
    """Redact again after parsing: credential-looking keys and text anywhere in a record."""
    if isinstance(value, dict):
        return {k: "[redacted]" if reader.SECRET_KEY.search(k) else scrub(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return reader.redact(value) if isinstance(value, str) else value


def render(command: str, payload: str, as_json: bool) -> str:
    items, meta = [], {}
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            item = scrub(json.loads(line))
        except ValueError:
            continue
        if isinstance(item, dict) and "_meta" in item:
            meta = item["_meta"]
        elif isinstance(item, dict):
            items.append(item)
    if as_json:
        body = [json.dumps(item, ensure_ascii=False) for item in items]
    else:
        formatter = {"list": format_listing, "errors": format_group}.get(command, format_record)
        body = [line for item in items for line in formatter(item)]
        if not items:
            body = ["(nothing matched)"]
    summary = {k: v for k, v in meta.items() if v not in (None, "", 0) or k == "matched"}
    if summary:
        body.append("-- " + " ".join(f"{k}={v}" for k, v in summary.items()))
    return "\n".join(body) + "\n"


def cap(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    kept = data[:limit].decode("utf-8", "ignore").rsplit("\n", 1)[0]
    return (kept + f"\n[output truncated: {len(data) - len(kept.encode())} more bytes; narrow "
            "with --since, --service, --level or --grep, or raise --max-bytes]\n")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    client = argparse.ArgumentParser(add_help=False)
    client.add_argument("--json", action="store_true")
    client.add_argument("--local", type=Path)
    client.add_argument("--max-bytes", type=int, default=DEFAULT_OUTPUT)
    options, remote = client.parse_known_args(argv)
    if not remote or remote[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    try:
        reader.parser().parse_args(remote)  # the same grammar the host enforces
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        payload = (fetch_local(remote, options.local) if options.local
                   else fetch_remote(remote))
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    limit = max(1024, min(options.max_bytes, MAX_OUTPUT))
    sys.stdout.write(cap(render(remote[0], payload, options.json), limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
