"""Every deployable component has a manifest that agrees with its code (tools/components.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("components_tool", REPO / "tools" / "components.py")
components = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = components  # pydantic resolves annotations through sys.modules
spec.loader.exec_module(components)


def test_every_component_directory_has_a_manifest():
    missing = [d.name for d in sorted((REPO / "components").iterdir())
               if d.is_dir() and not (d / "component.yaml").exists()]
    assert not missing, f"add component.yaml to: {missing}"


def test_manifests_agree_with_the_code():
    assert components.problems(components.load_all()) == []


def test_a_provider_mismatch_is_reported():
    [ingest_us] = [c for c in components.load_all() if c.name == "ingest-us"]
    broken = ingest_us.model_copy(update={"providers": ["schwab"]})
    assert any("providers" in issue for issue in components.problems([broken]))
