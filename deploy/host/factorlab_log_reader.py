#!/usr/bin/env python3
"""Read-only access to FactorLab component logs (stdlib only; Python 3.10+).

Installed as /usr/local/bin/factorlab-log-reader and run by sshd's ForceCommand for
the restricted ``factorlab-logs`` account, so the client's command line arrives in
SSH_ORIGINAL_COMMAND and nothing else can run. The account is in the factorlab-logs
group only (no docker, no sudo) and this program reads nothing outside LOG_ROOT and
the Docker image snapshot.

    list
    tail   --component C [--service S] [--since 2h|ISO] [--until ISO] [--level WARNING]
           [--grep TEXT] [--regex RE] [--run-id ID] [--lines N] [--exc]
    errors [--component C] [--since 24h] [--limit N]
    run    --run-id ID [--since 7d] [--lines N] [--exc]

Output is JSON Lines. Every command ends with one ``{"_meta": {...}}`` line that
records the window, counts and whether output was truncated. Windows are at most 30
days, at most 200 records are returned, and the output is capped at 64 KiB.
Credential-looking text is redacted again here, whatever the writer did.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shlex
import sys
from collections import deque
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_ROOT = Path("/var/log/factorlab")
SNAPSHOTS = (
    Path("/var/lib/factorlab/docker-images/snapshot.v2.json"),
    Path("/var/lib/factorlab/docker-images/snapshot.json"),
)
MAX_WINDOW = timedelta(days=30)
DEFAULT_LINES, MAX_LINES = 50, 200
MAX_OUTPUT = 64 * 1024
MAX_LINE = 64 * 1024  # longer lines are cut before parsing or matching
MAX_MESSAGE, MAX_EXCEPTION = 2_000, 12_000
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
COMPONENT = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
SERVICE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")
RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
RELATIVE = re.compile(r"^(\d{1,4})([mhd])$")
# Log files: <service>.jsonl or <service>.log, plus logrotate's -YYYYMMDD-HH[.gz]
# and the one-off -legacy.gz that prepare-host.sh migrates from the old cron log.
LOG_FILE = re.compile(
    r"^(?P<service>[a-z0-9][a-z0-9._-]{0,60}?)\.(?P<ext>jsonl|log)"
    r"(?:-(?P<stamp>\d{8}-\d{2}|legacy))?(?P<gz>\.gz)?$"
)
# Keep in step with factorlab.core.logging (tests/deploy/test_log_reader.py checks).
SECRET_KEY = re.compile(r"(?i)pass(word)?|secret|token|api[_-]?key|authorization|cookie")
SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+|access_token=|refresh_token=|api_key=|apikey=|password=|token=)"
    r"[^\s&\"',]+"
)
UUIDISH = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
NUMBERISH = re.compile(r"\b\d+(\.\d+)?\b|\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", re.IGNORECASE)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


class UsageError(Exception):
    pass


def redact(text: str) -> str:
    return SECRET_TEXT.sub(r"\1[redacted]", text)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str, now: datetime) -> datetime:
    match = RELATIVE.match(value)
    if match:
        amount, unit = int(match[1]), match[2]
        return now - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[unit]: amount})
    try:
        # "Z" is only accepted by fromisoformat from Python 3.11.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UsageError(f"not a time: {value!r} (use 30m, 2h, 7d or ISO 8601)") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def window(since: str, until: str | None, now: datetime) -> tuple[datetime, datetime]:
    start = parse_time(since, now)
    end = parse_time(until, now) if until else now
    if end < start:
        raise UsageError("--until is before --since")
    if end - start > MAX_WINDOW or now - start > MAX_WINDOW + timedelta(days=1):
        raise UsageError("the window is limited to the last 30 days")
    return start, end


# ── files ────────────────────────────────────────────────────────────────────


def component_dirs(root: Path, component: str | None = None) -> list[Path]:
    if component is not None and not COMPONENT.match(component):
        raise UsageError(f"invalid component {component!r}")
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_dir() or not COMPONENT.match(path.name):
            continue
        if component is None or path.name == component:
            found.append(path)
    if component is not None and not found:
        raise UsageError(f"no logs for component {component!r}")
    return found


def log_files(directory: Path, service: str | None = None) -> list[tuple[str, Path]]:
    """(service, path) in write order: rotated files by stamp, then the live file."""
    if service is not None and not SERVICE.match(service):
        raise UsageError(f"invalid service {service!r}")
    entries = []
    for path in directory.iterdir():
        match = LOG_FILE.match(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        if service is not None and match["service"] != service:
            continue
        # A live file sorts after its rotations; files of one service stay together.
        stamp = {None: "99999999-99", "legacy": "00000000-00"}.get(match["stamp"], match["stamp"])
        entries.append(((match["service"], match["ext"], stamp), match["service"], path))
    return [(name, path) for _, name, path in sorted(entries)]


def read_lines(path: Path) -> Iterator[str]:
    opener = gzip.open if path.name.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if line:
                    yield line[:MAX_LINE]
    except (OSError, EOFError):
        return  # rotated away or truncated mid-read: skip what cannot be read


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return datetime.min.replace(tzinfo=timezone.utc)


# ── records ──────────────────────────────────────────────────────────────────


def parse(line: str, component: str, service: str) -> dict:
    try:
        record = json.loads(line)
    except ValueError:
        record = None
    if not isinstance(record, dict):
        record = {"msg": line, "raw": True}
    record.setdefault("component", component)
    record.setdefault("service", service)
    return record


def timestamp(record: dict) -> datetime | None:
    value = record.get("ts")
    if not isinstance(value, str):
        return None
    try:
        # "Z" is only accepted by fromisoformat from Python 3.11.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def clean(record: dict, with_exception: bool) -> dict:
    """Redact, shorten and drop what the reader should not see."""
    out = {}
    for key, value in record.items():
        if SECRET_KEY.search(key):
            out[key] = "[redacted]"
        elif isinstance(value, str):
            out[key] = redact(value)
        else:
            out[key] = json.loads(redact(json.dumps(value, default=str)))
    if isinstance(out.get("msg"), str) and len(out["msg"]) > MAX_MESSAGE:
        out["msg"] = out["msg"][:MAX_MESSAGE] + " [cut]"
    exc = out.pop("exc", None)
    if isinstance(exc, str) and exc:
        lines = [line for line in exc.splitlines() if line.strip()]
        out["exc_summary"] = lines[-1][:500] if lines else ""
        if with_exception:
            out["exc"] = exc[-MAX_EXCEPTION:]
    return out


def records(
    root: Path, *, component: str | None, service: str | None, start: datetime, end: datetime
) -> Iterator[dict]:
    """Records inside the window, oldest file first; untimed lines follow their file."""
    for directory in component_dirs(root, component):
        for name, path in log_files(directory, service):
            if _mtime(path) < start:
                continue  # last written before the window opened
            for line in read_lines(path):
                record = parse(line, directory.name, name)
                moment = timestamp(record)
                if moment is not None and not start <= moment <= end:
                    continue
                yield record


# ── commands ─────────────────────────────────────────────────────────────────


def list_logs(root: Path, snapshots: tuple[Path, ...] = SNAPSHOTS) -> Iterator[dict]:
    for directory in component_dirs(root):
        files = []
        for name, path in log_files(directory):
            stat = path.stat()
            files.append(
                {
                    "service": name,
                    "file": path.name,
                    "bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        yield {"component": directory.name, "files": files}
    for snapshot in snapshots:
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        running = []
        for image in data.get("images", []):
            labels = image.get("labels") or {}
            for container in image.get("containers", []):
                running.append(
                    {
                        "service": container.get("service") or container.get("name"),
                        "status": container.get("status"),
                        "component": labels.get("component") or labels.get("title"),
                        "version": labels.get("version"),
                        "started_at": container.get("started_at"),
                    }
                )
        yield {
            "containers": sorted(running, key=lambda c: str(c["service"])),
            "snapshot_at": data.get("snapshot_at"),
            "snapshot": snapshot.name,
        }
        break


def tail(root: Path, args: argparse.Namespace, now: datetime) -> tuple[list[dict], dict]:
    start, end = window(args.since, args.until, now)
    level = LEVELS.get((args.level or "DEBUG").upper())
    if level is None:
        raise UsageError(f"unknown level {args.level!r}")
    if args.regex and len(args.regex) > 200:
        raise UsageError("--regex is limited to 200 characters")
    try:
        pattern = re.compile(args.regex) if args.regex else None
    except re.error as exc:
        raise UsageError(f"invalid --regex: {exc}") from None
    if args.run_id and not RUN_ID.match(args.run_id):
        raise UsageError("invalid --run-id")
    needle = args.grep.lower() if args.grep else None
    kept: deque[dict] = deque(maxlen=_lines(args.lines))
    matched = 0
    for record in records(
        root, component=args.component, service=args.service, start=start, end=end
    ):
        if LEVELS.get(str(record.get("level", "INFO")).upper(), 20) < level:
            continue
        if args.run_id and str(record.get("run_id", "")) != args.run_id:
            continue
        if needle or pattern:
            text = json.dumps(record, ensure_ascii=False)
            if needle and needle not in text.lower():
                continue
            if pattern and not pattern.search(text):
                continue
        matched += 1
        kept.append(clean(record, args.exc))
    return list(kept), {
        "since": start.isoformat(timespec="seconds"),
        "until": end.isoformat(timespec="seconds"),
        "matched": matched,
    }


def fingerprint(record: dict) -> str:
    message = str(record.get("msg", ""))
    message = QUOTED.sub("'…'", UUIDISH.sub("<id>", message))
    return NUMBERISH.sub("<n>", message)[:200]


def errors(root: Path, args: argparse.Namespace, now: datetime) -> tuple[list[dict], dict]:
    start, end = window(args.since, None, now)
    groups: dict[tuple, dict] = {}
    total = 0
    for record in records(root, component=args.component, service=None, start=start, end=end):
        if LEVELS.get(str(record.get("level", "INFO")).upper(), 20) < LEVELS["ERROR"]:
            continue
        total += 1
        key = (
            record.get("component"),
            record.get("service"),
            record.get("logger"),
            fingerprint(record),
        )
        seen = record.get("ts")
        group = groups.get(key)
        if group is None:
            cleaned = clean(record, False)
            groups[key] = group = {
                "component": key[0],
                "service": key[1],
                "logger": key[2],
                "fingerprint": redact(key[3]),
                "count": 0,
                "first": seen,
                "last": seen,
                "sample": cleaned.get("msg"),
                "exc_summary": cleaned.get("exc_summary"),
                "run_id": cleaned.get("run_id"),
            }
        group["count"] += 1
        group["last"] = seen or group["last"]
    ranked = sorted(groups.values(), key=lambda g: (-g["count"], str(g["last"])))
    return ranked[: max(1, min(args.limit, 100))], {
        "since": start.isoformat(timespec="seconds"),
        "errors": total,
        "groups": len(groups),
    }


def run(root: Path, args: argparse.Namespace, now: datetime) -> tuple[list[dict], dict]:
    if not RUN_ID.match(args.run_id):
        raise UsageError("invalid --run-id")
    args = argparse.Namespace(
        **vars(args), component=None, service=None, until=None, level=None, grep=None, regex=None
    )
    return tail(root, args, now)


def _lines(value: int) -> int:
    return max(1, min(value, MAX_LINES))


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="factorlab-log-reader", add_help=True, description=__doc__.split("\n\n")[0]
    )
    commands = top.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    t = commands.add_parser("tail")
    t.add_argument("--component", required=True)
    t.add_argument("--service")
    t.add_argument("--since", default="24h")
    t.add_argument("--until")
    t.add_argument("--level")
    t.add_argument("--grep")
    t.add_argument("--regex")
    t.add_argument("--run-id")
    t.add_argument("--lines", type=int, default=DEFAULT_LINES)
    t.add_argument("--exc", action="store_true", help="include full tracebacks")
    e = commands.add_parser("errors")
    e.add_argument("--component")
    e.add_argument("--since", default="24h")
    e.add_argument("--limit", type=int, default=30)
    r = commands.add_parser("run")
    r.add_argument("--run-id", required=True)
    r.add_argument("--since", default="7d")
    r.add_argument("--lines", type=int, default=MAX_LINES)
    r.add_argument("--exc", action="store_true")
    return top


def execute(
    argv: list[str],
    root: Path = LOG_ROOT,
    now: datetime | None = None,
    snapshots: tuple[Path, ...] = SNAPSHOTS,
) -> tuple[list[dict], dict]:
    args = parser().parse_args(argv)
    now = now or _now()
    if args.command == "list":
        return list(list_logs(root, snapshots)), {}
    if args.command == "tail":
        return tail(root, args, now)
    if args.command == "errors":
        return errors(root, args, now)
    return run(root, args, now)


def emit(items: list[dict], meta: dict, stream=sys.stdout) -> None:
    written, truncated = 0, 0
    for item in items:
        line = json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))
        if written + len(line) + 1 > MAX_OUTPUT:
            truncated += 1
            continue
        stream.write(line + "\n")
        written += len(line) + 1
    stream.write(
        json.dumps({"_meta": {**meta, "returned": len(items) - truncated, "truncated": truncated}})
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        original = os.environ.get("SSH_ORIGINAL_COMMAND")
        try:
            argv = shlex.split(original) if original is not None else sys.argv[1:]
        except ValueError:
            print("error: could not parse the command line", file=sys.stderr)
            return 2
    if argv[:1] == ["factorlab-log-reader"]:
        argv = argv[1:]
    try:
        items, meta = execute(argv)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    emit(items, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
