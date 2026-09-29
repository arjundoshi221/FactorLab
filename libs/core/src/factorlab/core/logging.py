"""Structured logging for every FactorLab component (stdlib only).

Call :func:`configure_logging` once at process start. Every record becomes one JSON
object per line on stderr (Docker captures it; stdout stays free for a command's own
output) and, when a log directory is configured, in ``<log_dir>/<service>.jsonl``.

The file is opened with :class:`logging.handlers.WatchedFileHandler`, which
reopens it after the host's logrotate renames it. So rotation needs neither a
signal nor ``copytruncate``, which can drop lines.

Fields: ``ts`` (UTC, ms), ``level``, ``component``, ``service``, ``version``,
``commit``, ``logger``, ``msg``, ``exc``. Also any ``extra=`` passed to the log call
and whatever :func:`log_context` binds, for example ``run_id``, ``pipeline``,
``source`` and ``request_id``. Values that look like secrets are redacted.

Environment defaults:

    FACTORLAB_COMPONENT   component name, e.g. ``ingest-us`` (images set it)
    FACTORLAB_SERVICE     compose service, e.g. ``universe-us`` (defaults to the component)
    FACTORLAB_LOG_LEVEL   default INFO
    FACTORLAB_LOG_DIR     enables the file sink, e.g. /var/log/factorlab/ingest-us
    FACTORLAB_LOG_FORMAT  ``json`` (default) or ``text`` (the default on an interactive terminal)
    FACTORLAB_VERSION / FACTORLAB_COMMIT   release metadata stamped by the image build
                          (FACTORLAB_RELEASE_ID stands in for the version on the monolith)

Logging must never stop ingestion: if the file sink cannot be opened the process
keeps logging to stderr and says so once.
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, TextIO

_CONTEXT: ContextVar[Mapping[str, Any]] = ContextVar(
    "factorlab_log_context", default=MappingProxyType({})
)
# Standard LogRecord attributes, plus uvicorn's ANSI-coloured duplicate of the message.
_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}
_SECRET_KEY = re.compile(r"(?i)pass(word)?|secret|token|api[_-]?key|authorization|cookie")
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+|access_token=|refresh_token=|api_key=|apikey=|password=|token=)"
    r"[^\s&\"',]+"
)
_MAX_MESSAGE = 16_384
_MAX_EXCEPTION = 32_768
_QUIET = ("urllib3", "clickhouse_connect", "httpx", "httpcore", "ib_async", "asyncio")
_HANDLER_TAG = "_factorlab_handler"


def redact(text: str) -> str:
    """Replace credential-looking values in free text (URLs, headers, messages)."""
    return _SECRET_TEXT.sub(r"\1[redacted]", text)


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (``run_id=...``, ``pipeline=...``) to every record logged inside."""
    token = _CONTEXT.set({**_CONTEXT.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def bind(**fields: Any) -> None:
    """Attach fields for the rest of the current context (thread or task)."""
    _CONTEXT.set({**_CONTEXT.get(), **{k: v for k, v in fields.items() if v is not None}})


def unbind(*names: str) -> None:
    """Remove fields attached with :func:`bind`."""
    _CONTEXT.set({k: v for k, v in _CONTEXT.get().items() if k not in names})


class JsonFormatter(logging.Formatter):
    """One compact JSON object per record."""

    def __init__(self, static: Mapping[str, str]) -> None:
        super().__init__()
        self._static = {k: v for k, v in static.items() if v}

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            **self._static,
            "logger": record.name,
            "msg": redact(record.getMessage())[:_MAX_MESSAGE],
        }
        extras = {
            **_CONTEXT.get(),
            **{
                k: v
                for k, v in vars(record).items()
                if k not in _RESERVED and not k.startswith("_")
            },
        }
        for key, value in extras.items():
            doc.setdefault(key, "[redacted]" if _SECRET_KEY.search(key) else value)
        if record.exc_info:
            doc["exc"] = redact(self.formatException(record.exc_info))[:_MAX_EXCEPTION]
        return json.dumps(doc, default=str, ensure_ascii=False, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human-readable lines for interactive terminals."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        line = redact(super().format(record))
        context = {**_CONTEXT.get()}
        return f"{line}  {context}" if context else line


class _WatchedFile(logging.handlers.WatchedFileHandler):
    """Append-only, line-buffered, created 0640 (group-readable for the log reader)."""

    def _open(self) -> TextIO:
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        return open(descriptor, "a", encoding="utf-8", buffering=1)


def configure_logging(
    *,
    component: str | None = None,
    service: str | None = None,
    level: str | int | None = None,
    log_dir: str | Path | None = None,
    fmt: str | None = None,
    stream: TextIO | None = None,
) -> None:
    """Install FactorLab's handlers on the root logger (idempotent).

    Arguments override the ``FACTORLAB_*`` environment defaults described in the
    module docstring. Only handlers this function installed are replaced, so a
    test harness's own handlers (``caplog``) survive.
    """
    component = component or os.getenv("FACTORLAB_COMPONENT") or "factorlab"
    service = service or os.getenv("FACTORLAB_SERVICE") or component
    level = level or os.getenv("FACTORLAB_LOG_LEVEL") or "INFO"
    directory = log_dir or os.getenv("FACTORLAB_LOG_DIR") or None
    stream = stream or sys.stderr
    fmt = (
        fmt
        or os.getenv("FACTORLAB_LOG_FORMAT")
        or ("text" if getattr(stream, "isatty", lambda: False)() else "json")
    )
    static = {
        "component": component,
        "service": service,
        # Per-component images set FACTORLAB_VERSION; the monolith only a release id.
        "version": os.getenv("FACTORLAB_VERSION") or os.getenv("FACTORLAB_RELEASE_ID", ""),
        "commit": os.getenv("FACTORLAB_COMMIT", "")[:12],
    }
    formatter: logging.Formatter = TextFormatter() if fmt == "text" else JsonFormatter(static)

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(handler)
        handler.close()
    console = logging.StreamHandler(stream)
    handlers: list[logging.Handler] = [console]
    problem = None
    if directory:
        try:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            handlers.append(_WatchedFile(path / f"{service}.jsonl", encoding="utf-8"))
        except OSError as exc:
            problem = exc
    for handler in handlers:
        handler.setFormatter(formatter if handler is console else JsonFormatter(static))
        setattr(handler, _HANDLER_TAG, True)
        root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else level.upper())
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    _install_exception_hooks()
    if problem is not None:
        logging.getLogger(__name__).warning(
            "file logging disabled; continuing on stderr only: %s", problem
        )


def _install_exception_hooks() -> None:
    def excepthook(kind, value, tb):  # type: ignore[no-untyped-def]
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, tb)
            return
        logging.getLogger("factorlab.uncaught").critical(
            "uncaught exception", exc_info=(kind, value, tb)
        )

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        logging.getLogger("factorlab.uncaught").critical(
            "uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


__all__ = [
    "JsonFormatter",
    "TextFormatter",
    "bind",
    "configure_logging",
    "log_context",
    "redact",
    "unbind",
]
