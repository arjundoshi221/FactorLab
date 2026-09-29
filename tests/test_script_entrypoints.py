"""Every ``scripts/*.py`` path production invokes exists and forwards to importable code.

Compose commands, the release deployer and cron call these paths; the code behind
them lives in the ``factorlab`` package, so a moved module must not orphan one.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CALLERS = [
    REPO / "deploy" / "compose.production.yml",
    REPO / "docker-compose.yml",
    *sorted((REPO / "deploy" / "scripts").glob("*.sh")),
    *sorted((REPO / "deploy" / "cron").glob("*")),
]
SCRIPT_REF = re.compile(r"scripts/[\w/]+\.py")


def _referenced_scripts() -> list[str]:
    found: set[str] = set()
    for caller in CALLERS:
        if caller.is_file():
            found.update(SCRIPT_REF.findall(caller.read_text(encoding="utf-8")))
    # deploy/scripts/*.sh are host scripts, not python entry points
    return sorted(ref for ref in found if not ref.startswith("deploy/"))


REFERENCED = _referenced_scripts()


def test_production_invokes_known_scripts():
    assert "scripts/factlab_india_clickhouse_5min.py" in REFERENCED
    assert "scripts/verify_clickhouse_v2_writes.py" in REFERENCED


@pytest.mark.parametrize("script", REFERENCED)
def test_script_forwards_to_an_importable_entry_point(script):
    path = REPO / script
    assert path.is_file(), f"{script} is invoked by production but missing"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets = [(node.module, alias.name) for node in tree.body
               if isinstance(node, ast.ImportFrom) and node.module
               and node.module.startswith("factorlab.") for alias in node.names]
    assert targets, f"{script} should forward to a factorlab module"
    for module, name in targets:
        assert callable(getattr(importlib.import_module(module), name))
