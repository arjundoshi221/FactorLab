"""Single source of truth for on-disk paths.

All raw vendor dumps, state checkpoints, log files, and token files MUST be
resolved through this module. Hardcoded ``data/<source>/raw`` literals are
forbidden in source / scripts (enforced by ``scripts/_check_paths_in_code.py``).

Every default hangs off FactorLab's *home* (:func:`discover_home`):
``FACTORLAB_HOME`` when set (images set ``/app``), otherwise the checkout that
contains the working directory. It is never derived from where the package is
installed, so an installed wheel resolves the same paths as a checkout.

Env knobs:

    FACTORLAB_HOME              default: the enclosing checkout, else the working directory
    FACTORLAB_CONFIG_DIR        default: <home>/configs
    FACTORLAB_RAW_ROOT          default: <home>/data
    FACTORLAB_STATE_ROOT        default: <home>/data/_state
    FACTORLAB_TOKEN_ROOT        default: <home>/data
    FACTORLAB_LOG_ROOT          default: <home>/logs
    FACTORLAB_HEARTBEAT_ROOT    default: <home>/data/_heartbeat

Per-source layout maps preserve the existing (slightly heterogeneous) on-disk
shape. Today's reality:

    raw/political:            <RAW_ROOT>/political/raw
    raw/political/fec:        <RAW_ROOT>/political/raw/fec
    raw/political/senate_efd: <RAW_ROOT>/political/raw/senate_efd
    raw/eodhd:                <RAW_ROOT>/eodhd
    raw/upstox/instruments:   <RAW_ROOT>/upstox/instruments
    raw/upstox/live:          <RAW_ROOT>/in/live
    state/political:          <home>/data/political/_state    (legacy)
    state/<other>:            <STATE_ROOT>/<other>
    token/upstox:             <home>/data/upstox/.token
    token/upstox_auth_code:   <home>/data/upstox/.auth_code
    token/schwab:             <home>/data/schwab/.token       (also via SCHWAB_TOKEN_PATH)

A later phase can decommission the per-source maps once data is migrated to
the unified shape.

Security: every getter passes its result through :func:`assert_under_root`,
which refuses paths that escape the configured root (defense against
``..``-injection in upstream identifiers).
"""

from __future__ import annotations

import os
from pathlib import Path

# A checkout is recognised by the ingestion bindings file it always carries.
_HOME_MARKER = Path("configs") / "ingestion" / "bindings.yaml"


def discover_home(start: Path | None = None) -> Path:
    """Return ``FACTORLAB_HOME``, else the checkout enclosing *start* (default: cwd)."""
    configured = os.getenv("FACTORLAB_HOME")
    if configured:
        return Path(configured).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / _HOME_MARKER).is_file():
            return candidate
    return here


HOME: Path = discover_home()
# Older name for HOME, kept for existing callers.
REPO_ROOT: Path = HOME
CONFIG_DIR: Path = Path(os.getenv("FACTORLAB_CONFIG_DIR", str(HOME / "configs")))

# ── Env-configurable roots (defaults = legacy in-repo paths) ────────────────

RAW_ROOT: Path = Path(os.getenv("FACTORLAB_RAW_ROOT", str(HOME / "data")))
STATE_ROOT: Path = Path(os.getenv("FACTORLAB_STATE_ROOT", str(HOME / "data" / "_state")))
TOKEN_ROOT: Path = Path(os.getenv("FACTORLAB_TOKEN_ROOT", str(HOME / "data")))
LOG_ROOT: Path = Path(os.getenv("FACTORLAB_LOG_ROOT", str(HOME / "logs")))
HEARTBEAT_ROOT: Path = Path(
    os.getenv("FACTORLAB_HEARTBEAT_ROOT", str(HOME / "data" / "_heartbeat"))
)

# ── Per-source legacy layout maps (Phase 1: preserve existing shape) ────────

_RAW_LAYOUT: dict[str, str] = {
    "political": "political/raw",
    "fec": "political/raw/fec",
    "senate_efd": "political/raw/senate_efd",
    "senate_efd_ptrs": "political/raw/senate_efd/ptrs",
    "senate_efd_llm": "political/raw/senate_efd/_llm_extractions",
    "eodhd": "eodhd",
    "upstox_instruments": "upstox/instruments",
    "upstox_live": "in/live",
}

# Sources whose state still lives in a vendor-nested directory (legacy).
# Anything not in this map uses the unified <STATE_ROOT>/<source>/ shape.
_STATE_LEGACY: dict[str, Path] = {
    "political": HOME / "data" / "political" / "_state",
}

_TOKEN_LAYOUT: dict[str, str] = {
    "upstox": "upstox/.token",
    "upstox_auth_code": "upstox/.auth_code",
    "schwab": "schwab/.token",
}


# ── Public API ──────────────────────────────────────────────────────────────


def home() -> Path:
    """FactorLab's home directory (see module docstring)."""
    return HOME


def config_dir() -> Path:
    """Directory holding ``ingestion/``, ``sources/`` and ``universes/`` configuration."""
    return CONFIG_DIR


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
        raise PermissionError(f"path escapes root: {resolved} not under {root_resolved}") from e
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
    return assert_under_root(p, HOME)


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
    """Return the log directory (``FACTORLAB_LOG_ROOT``, default ``<home>/logs``)."""
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return LOG_ROOT


def heartbeat_path(service: str) -> Path:
    """Return the heartbeat file path for *service*. Liveness via ``mtime``."""
    HEARTBEAT_ROOT.mkdir(parents=True, exist_ok=True)
    p = HEARTBEAT_ROOT / service
    return assert_under_root(p, HEARTBEAT_ROOT)


__all__ = [
    "CONFIG_DIR",
    "HEARTBEAT_ROOT",
    "HOME",
    "LOG_ROOT",
    "RAW_ROOT",
    "REPO_ROOT",
    "STATE_ROOT",
    "TOKEN_ROOT",
    "assert_under_root",
    "config_dir",
    "discover_home",
    "heartbeat_path",
    "home",
    "log_dir",
    "raw_dir",
    "state_dir",
    "token_path",
]
