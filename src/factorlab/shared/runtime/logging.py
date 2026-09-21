"""Standard logging setup for every FactorLab script.

Two helpers:

  setup_logging(name)         — dated file in LOG_ROOT + stdout, INFO by default
  dated_log_dir(prefix)       — per-run directory under LOG_ROOT (e.g. for the
                                political orchestrator's per-mode log bundles)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from factorlab.shared.paths import log_dir as _log_dir

_DEFAULT_FMT = "%(asctime)s %(levelname)-7s %(name)s -- %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"


def setup_logging(
    name: str,
    *,
    log_root: Path | None = None,
    level: int = logging.INFO,
    fmt: str = _DEFAULT_FMT,
    datefmt: str = _DEFAULT_DATEFMT,
    force: bool = True,
) -> logging.Logger:
    """Configure root logging to write to ``<log_root>/<name>_<UTCdate>.log``
    AND stdout. Returns the script-named logger.

    ``force=True`` is the default so re-imports (notably during tests) reset
    handlers cleanly.
    """
    root = log_root or _log_dir()
    root.mkdir(parents=True, exist_ok=True)
    log_file = root / f"{name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=force,
    )
    return logging.getLogger(name)


def dated_log_dir(prefix: str, *, base: Path | None = None) -> Path:
    """Return ``<base>/<prefix>_YYYYMMDD/``, creating it if missing.

    Used for per-run log bundles (orchestrator log + verify log + ad-hoc).
    Date is UTC. ``base`` defaults to ``LOG_ROOT``.
    """
    base = base or _log_dir()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    d = base / f"{prefix}_{today}"
    d.mkdir(parents=True, exist_ok=True)
    return d


__all__ = ["setup_logging", "dated_log_dir"]
