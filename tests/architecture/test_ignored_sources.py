"""No source file may be hidden by ``.gitignore``.

Hatchling builds wheels (and therefore images) from the files git does not
ignore, so an ignored module still works in an editable checkout but silently
disappears from production, even when the file is tracked. That happened once
already: the unanchored ``datasets/`` data pattern hid
``factorlab/shared/ingest/datasets/``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    "tests",
    "scripts",
    "configs",
    "libs",
    "providers",
    "components",
    "tools",
    "deploy",
    "cloudflare",
)
SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".sql",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".md",
        ".ts",
        ".tsx",
        ".css",
        ".html",
        ".sh",
        ".ps1",
        ".service",
        ".timer",
        ".xml",
        ".conf",
    }
)
# Local-only files that are ignored on purpose.
EXPECTED = frozenset({".env", "wrangler.toml"})
GENERATED_PARTS = frozenset(
    {"__pycache__", "node_modules", "dist", ".wrangler", ".pytest_cache", ".ruff_cache", ".venv"}
)


@pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs a git checkout"
)
def test_no_source_file_is_gitignored():
    roots = [root for root in SOURCE_ROOTS if (REPO / root).exists()]
    listed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            *roots,
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    hidden = sorted(
        path
        for path in listed
        if Path(path).suffix in SOURCE_SUFFIXES
        and Path(path).name not in EXPECTED
        and not GENERATED_PARTS.intersection(Path(path).parts)
    )
    assert not hidden, (
        "These files are ignored by .gitignore and would be missing from builds; "
        f"anchor or narrow the matching pattern: {hidden}"
    )
