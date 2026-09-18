"""Runtime secret loading with memory-backed file support.

Production containers receive secrets as individual files rendered by Vault
Agent into a shared tmpfs volume. Local development can continue using normal
environment variables and ``.env`` files.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_secret(name: str, default: str | None = None) -> str | None:
    """Return a secret from a file or environment variable.

    Resolution order:

    1. ``<NAME>_FILE`` when explicitly configured.
    2. ``FACTORLAB_SECRETS_DIR/<NAME>`` when a runtime directory is configured.
    3. The regular environment variable, for local development.
    4. ``default``.

    Empty files and empty environment variables are treated as missing.
    """

    explicit_path = os.getenv(f"{name}_FILE", "").strip()
    secret_dir = os.getenv("FACTORLAB_SECRETS_DIR", "").strip()
    candidates = [Path(explicit_path)] if explicit_path else []
    if secret_dir:
        candidates.append(Path(secret_dir) / name)

    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(f"Unable to read secret {name} from {path}: {exc}") from exc
        if value:
            return value

    value = os.getenv(name, "").strip()
    return value or default


def require_secret(name: str) -> str:
    """Return a non-empty secret or raise a configuration error."""

    value = get_secret(name)
    if not value:
        raise OSError(f"Required secret {name} is not available")
    return value
