"""Every provider package runs the source-conformance suite for itself."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _workspace import REPO


def test_every_provider_runs_its_conformance_suite():
    missing = []
    for package in sorted(REPO.glob("providers/*/src/factorlab/sources/*")):
        if not (package / "__init__.py").exists():
            continue
        suite = package.parents[3] / "tests" / "test_conformance.py"
        call = f'conformance_tests("{package.name}")'
        if not suite.exists() or call not in suite.read_text(encoding="utf-8"):
            missing.append(f"{suite.relative_to(REPO)} must call {call}")
    assert not missing, missing
