"""Backend-only HTTP runner that starts the IBKR Flex history sync on request.

The hub cannot reach IBKR Flex itself: the Flex token is IP-restricted to the VPS.
This service runs on the VPS ``backend`` network (no published port) and the api
proxies the hub's "Sync IBKR history" button to it::

    POST /runs          -> 202 {job}; 409 while a run is active; 429 inside the cooldown
    GET  /runs/latest   -> {state: idle|running|success|partial|failed, ...}
    GET  /healthz       -> 200

Each job runs ``factorlab-ingest-broker engine run --dataset broker.flex_activity
--market USA`` (via ``python -m``) as a subprocess, so the engine path (``raw.archive``,
``meta.ingestion_runs``, ``broker.*`` writes) is exactly the manual one. Job state
is in memory only; a restart forgets the last job, never the data.

Usage:
    factorlab-ingest-broker flex-runner                       # production
    factorlab-ingest-broker flex-runner --dry-run --listen 127.0.0.1:8091
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from factorlab.core.logging import configure_logging, log_context
from factorlab.runtime import ExitCode, GracefulShutdown, Heartbeat, supervised

SERVICE = "ibkr_flex_runner"
DEFAULT_LISTEN = "0.0.0.0:8091"
# The image's own interpreter and CLI, so PATH and venv activation never matter.
COMMAND = (
    sys.executable,
    "-m",
    "factorlab.components.ingest_broker.cli",
    "engine",
    "run",
    "--dataset",
    "broker.flex_activity",
    "--market",
    "USA",
)
# IBKR allows about one Flex request per second per token and answers 1018 when
# pushed; a statement also changes at most a few times a day.
DEFAULT_COOLDOWN_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 600.0
ERROR_TAIL = 500

log = logging.getLogger("factorlab.ibkr.flex_runner")


@dataclass(slots=True)
class Job:
    job_id: str
    started_at: datetime
    state: str = "running"
    finished_at: datetime | None = None
    run_id: str | None = None
    rows_written: int | None = None
    failed_units: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("started_at", "finished_at"):
            data[key] = data[key].isoformat() if data[key] else None
        return data


def parse_summary(stdout: str) -> dict[str, Any] | None:
    """The last JSON run summary ``engine run`` printed, or ``None``."""
    decoder = json.JSONDecoder()
    found = None
    for index, char in enumerate(stdout):
        if char != "{" or (index and stdout[index - 1] != "\n"):
            continue
        try:
            value, _ = decoder.raw_decode(stdout, index)
        except ValueError:
            continue
        if isinstance(value, dict) and "run_id" in value and "status" in value:
            found = value
    return found


class FlexRunner:
    """One Flex sync at a time, with a cooldown between starts."""

    def __init__(
        self,
        *,
        command: Sequence[str] = COMMAND,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.command = list(command)
        self.cooldown_seconds = cooldown_seconds
        self.timeout_seconds = timeout_seconds
        self._run = run
        self._clock = clock
        self._spawn = spawn or (
            lambda target: threading.Thread(target=target, name="flex-sync", daemon=True).start()
        )
        self._lock = threading.Lock()
        self._job: Job | None = None

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return self._job.as_dict() if self._job else {"state": "idle"}

    def start(self) -> tuple[int, dict[str, Any]]:
        """Start a sync: ``(202, job)``, or ``(409|429, detail)`` when refused."""
        now = self._clock()
        with self._lock:
            job = self._job
            if job and job.state == "running":
                return 409, {"detail": "A Flex sync is already running", "job": job.as_dict()}
            if job:
                wait = self.cooldown_seconds - (now - job.started_at).total_seconds()
                if wait > 0:
                    return 429, {
                        "detail": f"Flex sync cooldown: retry in {int(wait) + 1}s",
                        "retry_after": int(wait) + 1,
                        "job": job.as_dict(),
                    }
            job = self._job = Job(job_id=uuid.uuid4().hex, started_at=now)
            snapshot = job.as_dict()
        self._spawn(lambda: self._execute(job))
        return 202, snapshot

    def _execute(self, job: Job) -> None:
        with log_context(job_id=job.job_id):
            log.info("Flex sync started: %s", " ".join(self.command))
            result = self._outcome()
            with self._lock:
                job.finished_at = self._clock()
                for key, value in result.items():
                    setattr(job, key, value)
            log.info(
                "Flex sync finished: %s (run_id=%s, rows=%s)%s",
                job.state,
                job.run_id,
                job.rows_written,
                f": {job.error}" if job.error else "",
            )

    def _outcome(self) -> dict[str, Any]:
        try:
            done = self._run(
                self.command, capture_output=True, text=True, timeout=self.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            return {"state": "failed", "error": f"timed out after {self.timeout_seconds:.0f}s"}
        except OSError as exc:
            return {"state": "failed", "error": f"could not start: {type(exc).__name__}: {exc}"}
        summary = parse_summary(done.stdout or "")
        if summary is None:
            tail = (done.stderr or done.stdout or "").strip()[-ERROR_TAIL:]
            return {
                "state": "failed",
                "error": f"exit {done.returncode} with no run summary"
                + (f": {tail}" if tail else ""),
            }
        failed = list(summary.get("failed_units") or [])
        status = str(summary.get("status"))
        return {
            "state": status if status in {"success", "partial"} else "failed",
            "run_id": summary.get("run_id"),
            "rows_written": summary.get("rows_written"),
            "failed_units": failed,
            "error": "; ".join(f"{u.get('name')}: {u.get('error')}" for u in failed) or None,
        }


def make_handler(runner: FlexRunner) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "factorlab-flex-runner/1"

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if "retry_after" in body:
                self.send_header("Retry-After", str(body["retry_after"]))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send(200, {"ok": True})
            elif self.path == "/runs/latest":
                self._send(200, runner.latest())
            else:
                self._send(404, {"detail": "Not found"})

        def do_POST(self) -> None:
            if self.path != "/runs":
                self._send(404, {"detail": "Not found"})
                return
            self._send(*runner.start())

        def log_message(self, format: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), format % args)

    return Handler


def parse_listen(raw: str) -> tuple[str, int]:
    host, sep, port = raw.rpartition(":")
    if not sep or not port.isdigit():
        raise argparse.ArgumentTypeError(f"invalid --listen {raw!r} (want HOST:PORT)")
    return host or "0.0.0.0", int(port)  # noqa: S104 - backend-network container


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--listen", type=parse_listen, default=parse_listen(DEFAULT_LISTEN))
    parser.add_argument("--cooldown-seconds", type=float, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--dry-run", action="store_true", help="Run the engine with --dry-run (writes nothing)"
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(component="ingest-broker", service="ibkr-flex-runner", level=args.log_level)
    runner = FlexRunner(
        command=[*COMMAND, *(["--dry-run"] if args.dry_run else [])],
        cooldown_seconds=args.cooldown_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    server = ThreadingHTTPServer(args.listen, make_handler(runner))
    threading.Thread(target=server.serve_forever, name="flex-http", daemon=True).start()
    log.info("Flex runner listening on %s:%s", *args.listen)
    heartbeat = Heartbeat(SERVICE)
    try:
        with GracefulShutdown(log) as shutdown:
            while not shutdown.triggered:
                heartbeat.tick()
                time.sleep(1.0)
    finally:
        server.shutdown()
        server.server_close()
    return int(ExitCode.OK)


def cli(argv: list[str] | None = None) -> int:
    """Process entry point: run :func:`main` under crash supervision."""
    return supervised(lambda: main(argv), name=SERVICE)


if __name__ == "__main__":
    sys.exit(cli())
