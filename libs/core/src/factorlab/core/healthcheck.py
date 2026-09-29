"""``factorlab-healthcheck``: container HEALTHCHECK probes (stdlib only).

    factorlab-healthcheck heartbeat <service> [--max-age SECONDS]
    factorlab-healthcheck http <url> [--timeout SECONDS]
    factorlab-healthcheck files <path> [<path> ...] [--max-age SECONDS]

Exit 0 when healthy, 1 otherwise (the Docker HEALTHCHECK contract). A heartbeat is
the mtime of ``<FACTORLAB_HEARTBEAT_ROOT>/<service>``, which daemons touch through
``factorlab.runtime.Heartbeat``.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from factorlab.core.paths import heartbeat_path


def _fresh(path: Path, max_age: float) -> str | None:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return f"{path} is missing"
    return None if age <= max_age else f"{path} is {age:.0f}s old (limit {max_age:.0f}s)"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factorlab-healthcheck", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="check", required=True)
    beat = commands.add_parser("heartbeat", help="a daemon's heartbeat file is recent")
    beat.add_argument("service")
    beat.add_argument("--max-age", type=float, default=300)
    http = commands.add_parser("http", help="a URL answers 2xx")
    http.add_argument("url")
    http.add_argument("--timeout", type=float, default=5)
    files = commands.add_parser("files", help="files exist (and are recent with --max-age)")
    files.add_argument("paths", nargs="+", type=Path)
    files.add_argument("--max-age", type=float)
    args = parser.parse_args(argv)

    problem: str | None = None
    if args.check == "heartbeat":
        problem = _fresh(heartbeat_path(args.service), args.max_age)
    elif args.check == "http":
        try:
            with urllib.request.urlopen(args.url, timeout=args.timeout) as response:
                if not 200 <= response.status < 300:
                    problem = f"{args.url} answered {response.status}"
        except Exception as exc:  # noqa: BLE001 - any failure is unhealthy
            problem = f"{args.url}: {exc}"
    else:
        for path in args.paths:
            if not path.exists():
                problem = f"{path} is missing"
            elif args.max_age is not None:
                problem = _fresh(path, args.max_age)
            if problem:
                break
    if problem:
        print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
