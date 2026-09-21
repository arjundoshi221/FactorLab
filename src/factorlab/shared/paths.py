"""Single source of truth for on-disk paths.

All raw vendor dumps, state checkpoints, log files, and token files MUST be
resolved through this module. Hardcoded ``data/<source>/raw`` literals are
forbidden in source / scripts (enforced by ``scripts/_check_paths_in_code.py``).

Env knobs (defaults are the canonical / primary repo-local layout):

    FACTORLAB_RAW_ROOT          default: <repo>/data
    FACTORLAB_STATE_ROOT        default: <repo>/data/_state
    FACTORLAB_TOKEN_ROOT        default: <repo>/data
    FACTORLAB_LOG_ROOT          default: <repo>/logs
    FACTORLAB_HEARTBEAT_ROOT    default: <repo>/data/_heartbeat

The repo is the **canonical / primary** location for raw vendor data. NAS
(``E:/NAS/factorlab/raw/``) is a one-way additive backup mirror populated by
``scripts/_shared/sync_raw_to_nas.py``; the application **never reads from
NAS at runtime**. ``FACTORLAB_RAW_ROOT`` should not be flipped to NAS — keep
defaults so the system keeps working when NAS is unavailable. The NAS sync
script reads its own separate env var (``FACTORLAB_NAS_BACKUP_ROOT``).

Per-source layout maps preserve the existing (slightly heterogeneous) on-disk
shape. Today's reality:

    raw/political:            <RAW_ROOT>/political/raw
    raw/political/fec:        <RAW_ROOT>/political/raw/fec
    raw/political/senate_efd: <RAW_ROOT>/political/raw/senate_efd
    raw/eodhd:                <RAW_ROOT>/eodhd
    raw/upstox/instruments:   <RAW_ROOT>/upstox/instruments
    raw/upstox/live:          <RAW_ROOT>/in/live
    state/political:          <REPO_ROOT>/data/political/_state    (legacy)
    state/<other>:            <STATE_ROOT>/<other>
    token/upstox:             <REPO_ROOT>/data/upstox/.token
    token/upstox_auth_code:   <REPO_ROOT>/data/upstox/.auth_code
    token/schwab:             <REPO_ROOT>/data/schwab/.token       (also via SCHWAB_TOKEN_PATH)

A later phase can decommission the per-source maps once data is migrated to
the unified shape.

Security: every getter passes its result through :func:`assert_under_root`,
which refuses paths that escape the configured root (defense against
``..``-injection in upstream identifiers).
"""

from __future__ import annotations

import os
from pathlib import Path

# ``parents[3]`` walks: paths.py → shared/ → factorlab/ → src/ → <repo root>
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# ── Env-configurable roots (defaults = legacy in-repo paths) ────────────────

RAW_ROOT: Path = Path(os.getenv("FACTORLAB_RAW_ROOT", str(REPO_ROOT / "data")))
STATE_ROOT: Path = Path(os.getenv("FACTORLAB_STATE_ROOT", str(REPO_ROOT / "data" / "_state")))
TOKEN_ROOT: Path = Path(os.getenv("FACTORLAB_TOKEN_ROOT", str(REPO_ROOT / "data")))
LOG_ROOT: Path = Path(os.getenv("FACTORLAB_LOG_ROOT", str(REPO_ROOT / "logs")))
HEARTBEAT_ROOT: Path = Path(
    os.getenv("FACTORLAB_HEARTBEAT_ROOT", str(REPO_ROOT / "data" / "_heartbeat"))
)

# ── Per-source legacy layout maps (Phase 1: preserve existing shape) ────────

_RAW_LAYOUT: dict[str, str] = {
    "political":          "political/raw",
    "fec":                "political/raw/fec",
    "senate_efd":         "political/raw/senate_efd",
    "senate_efd_ptrs":    "political/raw/senate_efd/ptrs",
    "senate_efd_llm":     "political/raw/senate_efd/_llm_extractions",
    "eodhd":              "eodhd",
    "upstox_instruments": "upstox/instruments",
    "upstox_live":        "in/live",
}

# Sources whose state still lives in a vendor-nested directory (legacy).
# Anything not in this map uses the unified <STATE_ROOT>/<source>/ shape.
_STATE_LEGACY: dict[str, Path] = {
    "political": REPO_ROOT / "data" / "political" / "_state",
}

_TOKEN_LAYOUT: dict[str, str] = {
    "upstox":            "upstox/.token",
    "upstox_auth_code":  "upstox/.auth_code",
    "schwab":            "schwab/.token",
}


# ── Public API ──────────────────────────────────────────────────────────────


def assert_under_root(path: Path, root: Path) -> Path:
    """Resolve *path* and refuse if it escapes *root*.

    Returns the absolute, resolved path. Raises ``PermissionError`` on escape
    (e.g. ``..``-injection in an upstream filename or identifier).
    """
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as e:
        raise PermissionError(
            f"path escapes root: {resolved} not under {root_resolved}"
        ) from e
    return resolved


def raw_dir(source: str) -> Path:
    """Return the on-disk directory for raw vendor data of *source*.

    Creates the directory on first use. Refuses if the source key resolves
    outside ``FACTORLAB_RAW_ROOT``.
    """
    subpath = _RAW_LAYOUT.get(source, source)
    p = RAW_ROOT / subpath
    p.mkdir(parents=True, exist_ok=True)
    return assert_under_root(p, RAW_ROOT)


def state_dir(source: str) -> Path:
    """Return the directory holding resume checkpoints for *source*."""
    legacy = _STATE_LEGACY.get(source)
    p = legacy if legacy is not None else STATE_ROOT / source
    p.mkdir(parents=True, exist_ok=True)
    return assert_under_root(p, REPO_ROOT)


def token_path(source: str) -> Path:
    """Return the path to the token file for *source*.

    Tokens stay under the repo per the project decision (small, frequently
    rewritten, must live near the code that reads them).
    """
    subpath = _TOKEN_LAYOUT.get(source, f"_tokens/.{source}.token")
    p = TOKEN_ROOT / subpath
    p.parent.mkdir(parents=True, exist_ok=True)
    return assert_under_root(p, TOKEN_ROOT)


def log_dir() -> Path:
    """Return the log directory (``FACTORLAB_LOG_ROOT``, default ``<repo>/logs``)."""
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return LOG_ROOT


def heartbeat_path(service: str) -> Path:
    """Return the heartbeat file path for *service*. Liveness via ``mtime``."""
    HEARTBEAT_ROOT.mkdir(parents=True, exist_ok=True)
    p = HEARTBEAT_ROOT / service
    return assert_under_root(p, HEARTBEAT_ROOT)


__all__ = [
    "REPO_ROOT",
    "RAW_ROOT",
    "STATE_ROOT",
    "TOKEN_ROOT",
    "LOG_ROOT",
    "HEARTBEAT_ROOT",
    "assert_under_root",
    "raw_dir",
    "state_dir",
    "token_path",
    "log_dir",
    "heartbeat_path",
]
