#!/usr/bin/env python3
"""Copy third-party container and host-unit logs to /var/log/factorlab for the log reader.

The restricted factorlab-logs account can run neither docker nor journalctl, so this root
timer (factorlab-container-logs.timer, every minute) appends what each source wrote
since the previous run to ``/var/log/factorlab/<component>/<service>.jsonl``. Records
have the shape factorlab.core.logging writes: ts, level, component, service, logger,
msg, plus ``stream``. Lines below INFO are dropped (ClickHouse logs at trace level),
credential-looking text is redacted, and each source keeps at most MAX_RECORDS per run,
so a log storm cannot fill the disk. Dropped lines are counted in one WARNING record.

Cursors live in STATE. A source seen for the first time starts LOOKBACK ago. A missing
container (an unused profile) is skipped. stdlib only; Python 3.10+.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

LOG_ROOT = Path("/var/log/factorlab")
STATE = Path("/var/lib/factorlab/container-logs/state.json")
LOCK = Path("/run/lock/factorlab-container-logs.lock")
GROUP = "factorlab-logs"
# compose service -> (log directory, service name); containers of project "factorlab".
CONTAINERS = {
    "clickhouse": ("clickhouse", "clickhouse"),
    "ibkr-gateway-paper": ("ibkr-gateway", "paper"),
    "ibkr-gateway-live": ("ibkr-gateway", "live"),
    "uptime-kuma": ("ops", "uptime-kuma"),
    "portainer": ("ops", "portainer"),
}
# systemd unit -> (log directory, service name)
UNITS = {"factorlab-cloudflared.service": ("edge", "cloudflared")}
LOG_DIRECTORIES = sorted({d for d, _ in [*CONTAINERS.values(), *UNITS.values()]})
LOOKBACK_NS = 10 * 60 * 10**9
SETTLE_NS = 2 * 10**9  # leave the last two seconds for the next run
MAX_RECORDS = 5_000
MAX_MESSAGE = 16_384
TIMEOUT = 50
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
# ClickHouse: "2026.10.23 10:00:00.123456 [ 1 ] {} <Error> Application: ..."
CLICKHOUSE_LEVEL = re.compile(
    r"<(Fatal|Critical|Error|Warning|Notice|Information|Debug|Trace|Test)>"
)
CLICKHOUSE_LEVELS = {
    "Fatal": "CRITICAL",
    "Critical": "CRITICAL",
    "Error": "ERROR",
    "Warning": "WARNING",
    "Notice": "INFO",
    "Information": "INFO",
    "Debug": "DEBUG",
    "Trace": "DEBUG",
    "Test": "DEBUG",
}
# IBC/Java, Uptime Kuma and cloudflared spell levels in capitals (cloudflared: ERR/WRN/INF/DBG).
WORD_LEVEL = re.compile(
    r"\b(FATAL|CRITICAL|SEVERE|ERROR|ERR|WARNING|WARN|WRN|INFO|INF|DEBUG|DBG|TRACE)\b"
)
WORD_LEVELS = {
    "FATAL": "CRITICAL",
    "CRITICAL": "CRITICAL",
    "SEVERE": "ERROR",
    "ERROR": "ERROR",
    "ERR": "ERROR",
    "WARNING": "WARNING",
    "WARN": "WARNING",
    "WRN": "WARNING",
    "INFO": "INFO",
    "INF": "INFO",
    "DEBUG": "DEBUG",
    "DBG": "DEBUG",
    "TRACE": "DEBUG",
}
# syslog PRIORITY 0-7
PRIORITIES = ["CRITICAL", "CRITICAL", "CRITICAL", "ERROR", "WARNING", "INFO", "INFO", "DEBUG"]
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Keep in step with factorlab.core.logging (tests/deploy/test_container_logs.py checks).
SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+|access_token=|refresh_token=|api_key=|apikey=|api_token=|password=|token=)"
    r"[^\s&\"',]+"
)
BOT_TOKEN = re.compile(r"(/bot)\d+:[\w-]+")

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def redact(text: str) -> str:
    return BOT_TOKEN.sub(r"\1[redacted]", SECRET_TEXT.sub(r"\1[redacted]", text))


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=TIMEOUT, check=False
    )


def level_of(text: str) -> str:
    match = CLICKHOUSE_LEVEL.search(text)
    if match:
        return CLICKHOUSE_LEVELS[match[1]]
    match = WORD_LEVEL.search(text[:300])
    return WORD_LEVELS[match[1]] if match else "INFO"


def iso(ns: int) -> str:
    moment = datetime.fromtimestamp(ns // 10**9, timezone.utc).replace(
        microsecond=(ns % 10**9) // 1000
    )
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def docker_ns(stamp: str) -> int:
    """Nanoseconds since the epoch from docker's RFC 3339 (UTC, up to 9 fraction digits)."""
    whole, _, fraction = stamp.rstrip("Z").partition(".")
    seconds = datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return int(seconds.timestamp()) * 10**9 + int((fraction + "000000000")[:9])


def record(ns: int, level: str, directory: str, service: str, logger: str, **fields) -> dict:
    return {
        "ts": iso(ns),
        "level": level,
        "component": directory,
        "service": service,
        "logger": logger,
        **fields,
    }


def keep(records: list[dict], directory: str, service: str, logger: str) -> list[dict]:
    """INFO and above, oldest first, capped; a WARNING notes what was dropped."""
    kept = [r for r in records if LEVELS[r["level"]] >= LEVELS["INFO"]]
    kept.sort(key=lambda r: r["_ns"])
    dropped = len(kept) - MAX_RECORDS
    if dropped > 0:
        kept = kept[dropped:]
        kept.insert(
            0,
            record(
                kept[0]["_ns"], "WARNING", directory, service, logger,
                msg=f"log collector dropped {dropped} older lines this minute", _ns=kept[0]["_ns"],
            ),
        )  # fmt: skip
    return kept


def collect_container(
    name: str, directory: str, service: str, cursor: int | None, now_ns: int, runner: Runner
) -> tuple[list[dict], int]:
    """New lines of one container since ``cursor`` (exclusive) and the next cursor."""
    since = cursor if cursor is not None else now_ns - LOOKBACK_NS
    until = now_ns - SETTLE_NS
    if until <= since:
        return [], since
    result = runner(
        [
            "docker", "logs", "--timestamps",
            "--since", f"{since // 10**9}.{since % 10**9:09d}",
            "--until", f"{until // 10**9}.{until % 10**9:09d}",
            name,
        ]
    )  # fmt: skip
    if result.returncode:
        raise RuntimeError(f"docker logs {name} exited {result.returncode}")
    records, last = [], since
    for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        for line in text.splitlines():
            stamp, _, message = line.partition(" ")
            try:
                ns = docker_ns(stamp)
            except ValueError:
                continue
            if ns <= since or ns > until:
                continue
            last = max(last, ns)
            message = redact(ANSI.sub("", message))[:MAX_MESSAGE]
            if message.strip():
                records.append(
                    record(ns, level_of(message), directory, service, "docker",
                           stream=stream, msg=message, _ns=ns)
                )  # fmt: skip
    # Nothing new: move up to the settled edge so later runs scan less.
    return keep(records, directory, service, "docker"), max(last, until) if not records else last


def collect_unit(
    unit: str, directory: str, service: str, cursor: str | None, runner: Runner
) -> tuple[list[dict], str | None]:
    """New journal entries of one systemd unit after ``cursor`` and the next cursor."""
    base = ["journalctl", "--unit", unit, "--output", "json", "--no-pager", "--quiet"]
    result = runner([*base, "--after-cursor", cursor]) if cursor else None
    if result is None or result.returncode:
        # First run, or the cursor was vacuumed away: restart from the lookback window.
        result = runner([*base, "--since", f"-{LOOKBACK_NS // 60 // 10**9}min"])
    if result.returncode:
        raise RuntimeError(f"journalctl {unit} exited {result.returncode}")
    records, last = [], cursor
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            ns = int(entry["__REALTIME_TIMESTAMP"]) * 1000
        except (ValueError, KeyError, TypeError):
            continue
        last = entry.get("__CURSOR", last)
        message = entry.get("MESSAGE")
        if not isinstance(message, str) or not message.strip():
            continue  # binary payloads arrive as byte lists
        try:
            level = PRIORITIES[int(entry.get("PRIORITY", 6))]
        except (ValueError, IndexError):
            level = "INFO"
        if level == "INFO":
            level = level_of(message)
        message = redact(ANSI.sub("", message))[:MAX_MESSAGE]
        records.append(record(ns, level, directory, service, "journal", msg=message, _ns=ns))
    return keep(records, directory, service, "journal"), last


def append(root: Path, directory: str, service: str, records: list[dict], gid: int | None) -> None:
    if not records:
        return
    folder = root / directory
    if not folder.is_dir():
        folder.mkdir(mode=0o2750, parents=True)
        if gid is not None:
            os.chown(folder, 0, gid)
            folder.chmod(0o2750)
    path = folder / f"{service}.jsonl"
    created = not path.exists()
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    with open(descriptor, "a", encoding="utf-8") as handle:
        if created and gid is not None:
            os.fchown(handle.fileno(), 0, gid)
        for item in records:
            item = {k: v for k, v in item.items() if not k.startswith("_")}
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".state-", delete=False
    ) as handle:
        json.dump(state, handle, sort_keys=True)
    Path(handle.name).chmod(0o600)
    Path(handle.name).replace(path)


def containers(runner: Runner) -> dict[str, str]:
    """compose service -> container name, for the project's containers (any state)."""
    result = runner(
        [
            "docker", "ps", "-a", "--filter", "label=com.docker.compose.project=factorlab",
            "--format", '{{.Label "com.docker.compose.service"}}\t{{.Names}}',
        ]
    )  # fmt: skip
    if result.returncode:
        raise RuntimeError(f"docker ps exited {result.returncode}")
    found = {}
    for line in result.stdout.splitlines():
        service, _, name = line.partition("\t")
        if service in CONTAINERS and name:
            found[service] = name
    return found


def collect(
    root: Path, state: dict, now_ns: int, runner: Runner, gid: int | None
) -> tuple[dict, list[str]]:
    """One pass over every source; returns the new state and the failures."""
    state = dict(state)
    failures = []
    try:
        names = containers(runner)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        names = {}
        failures.append(str(exc))
    for compose_service, name in sorted(names.items()):
        directory, service = CONTAINERS[compose_service]
        key = f"docker:{compose_service}"
        try:
            records, cursor = collect_container(
                name, directory, service, state.get(key), now_ns, runner
            )
            append(root, directory, service, records, gid)
            state[key] = cursor
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            failures.append(f"{compose_service}: {exc}")
    for unit, (directory, service) in sorted(UNITS.items()):
        key = f"journal:{unit}"
        try:
            records, cursor = collect_unit(unit, directory, service, state.get(key), runner)
            append(root, directory, service, records, gid)
            if cursor:
                state[key] = cursor
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            failures.append(f"{unit}: {exc}")
    return state, failures


def main() -> int:
    import fcntl
    import grp

    try:
        gid: int | None = grp.getgrnam(GROUP).gr_gid
    except KeyError:
        gid = None
    with LOCK.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # the previous run is still going
        state, failures = collect(LOG_ROOT, load_state(STATE), time.time_ns(), run, gid)
        save_state(STATE, state)
    for failure in failures:
        print(f"factorlab-container-logs: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
