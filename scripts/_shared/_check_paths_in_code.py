"""Pre-commit lint: refuse hardcoded raw-data paths in source / scripts.

Complements ``_check_no_data_paths.py`` (which blocks staging files under
``data/`` / ``logs/``). This one scans the **content** of staged Python files
and refuses any new hardcoded ``data/.../raw`` or ``data/<vendor>/`` literal
that should instead go through ``factorlab.core.paths``.

The single source of truth for on-disk locations is :mod:`factorlab.core.paths`.
If you need a new raw / state / token / log location, add it to the layout
maps in ``paths.py``, not to a literal in your script.

Receives staged file paths via argv (pre-commit framework convention).
Exits 1 on the first banned literal it finds, 0 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns we refuse to see in product code (libs/, providers/, components/) and scripts/.
# Each regex matches a hardcoded "data/<something>" literal that should
# instead be ``raw_dir("...")`` / ``state_dir("...")`` / ``token_path("...")``.
BANNED_PATTERNS = [
    re.compile(r'["\']data/political/raw[^"\']*["\']'),
    re.compile(r'["\']data/eodhd[^"\']*["\']'),
    re.compile(r'["\']data/upstox[^"\']*["\']'),
    re.compile(r'["\']data/schwab[^"\']*["\']'),
    re.compile(r'["\']data/in/live[^"\']*["\']'),
    re.compile(r'Path\(\s*["\']data/political/raw[^"\']*["\']'),
    re.compile(r'Path\(\s*["\']data/eodhd[^"\']*["\']'),
    re.compile(r'Path\(\s*["\']data/upstox[^"\']*["\']'),
    re.compile(r'Path\(\s*["\']data/schwab[^"\']*["\']'),
    re.compile(r'Path\(\s*["\']data/in/live[^"\']*["\']'),
]

# Files allowed to mention these literals (the helper module itself, this
# lint script, the migration script, and tests that exercise the mapping).
ALLOWED_FILES = {
    "libs/core/src/factorlab/core/paths.py",
    "scripts/_shared/_check_paths_in_code.py",
    "scripts/_shared/_check_no_data_paths.py",
    "tests/shared/test_paths.py",
}
POLICED_PREFIXES = ("libs/", "providers/", "components/", "scripts/")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_no, snippet) for banned literals found."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hits: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        # Skip docstrings / comments that talk about the legacy layout
        stripped = line.lstrip()
        if stripped.startswith(("#", '"""', "'''")):
            continue
        for pat in BANNED_PATTERNS:
            if pat.search(line):
                hits.append((line_no, line.strip()))
                break
    return hits


def main() -> int:
    staged = [p for p in sys.argv[1:] if p.endswith(".py")]
    if not staged:
        return 0

    failures: list[tuple[str, int, str]] = []
    for s in staged:
        norm = _norm(s)
        if norm in ALLOWED_FILES:
            continue
        if not norm.startswith(POLICED_PREFIXES):
            continue
        # Resolve relative to cwd (pre-commit runs at repo root)
        p = Path(s)
        if not p.exists():
            continue
        for line_no, snippet in _scan_file(p):
            failures.append((s, line_no, snippet))

    if not failures:
        return 0

    print(
        "ERROR: pre-commit blocked hardcoded raw-data path literals.\n"
        "Use factorlab.core.paths instead (raw_dir / state_dir / token_path).\n",
        file=sys.stderr,
    )
    for path, line_no, snippet in failures:
        print(f"  {path}:{line_no}: {snippet}", file=sys.stderr)
    print(
        "\nFix: import the helper and route through it, e.g."
        "\n  from factorlab.core.paths import raw_dir"
        '\n  CACHE_DIR = raw_dir("eodhd")',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
