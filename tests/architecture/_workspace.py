"""Where the workspace members keep their code, for the architecture scanners."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MEMBER_GROUPS = ("libs", "providers", "components")


def package_roots() -> list[Path]:
    """Every member's ``src/factorlab`` directory."""
    return sorted(root for group in MEMBER_GROUPS
                  for root in (REPO / group).glob("*/src/factorlab") if root.is_dir())


def python_files() -> Iterator[tuple[Path, str]]:
    """``(path, relative)`` for every product module; *relative* is under ``factorlab/``.

    ``relative`` looks the same wherever a member lives, e.g. ``storage/v2_us.py`` or
    ``components/api/app.py``, so rules can match on it.
    """
    for root in package_roots():
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path, path.relative_to(root).as_posix()


def module_name(path: Path) -> str:
    """Dotted module name of a product file (``factorlab.storage.v2_us``)."""
    for root in package_roots():
        if path.is_relative_to(root):
            parts = ["factorlab", *path.relative_to(root).with_suffix("").parts]
            if parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts)
    raise ValueError(f"{path} is not inside a workspace member")
