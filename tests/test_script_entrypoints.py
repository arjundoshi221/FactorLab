"""Everything production executes resolves to real code.

Compose services start component console scripts (``entrypoint: [factorlab-...]``);
deploy scripts and cron may still call ``scripts/*.py`` shims. A moved module or a
renamed entry point must not leave either pointing at nothing.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy" / "compose.production.yml"
CALLERS = [
    COMPOSE,
    REPO / "docker-compose.yml",
    *sorted((REPO / "deploy" / "scripts").glob("*.sh")),
    *sorted((REPO / "deploy" / "cron").glob("*")),
]
SCRIPT_REF = re.compile(r"scripts/[\w/]+\.py")


def _console_scripts() -> dict[str, str]:
    return {
        ep.name: ep.value
        for ep in importlib.metadata.entry_points(group="console_scripts")
        if ep.name.startswith("factorlab-")
    }


def _compose_entrypoints() -> list[tuple[str, str]]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    return sorted(
        (name, svc["entrypoint"][0])
        for name, svc in services.items()
        if svc.get("entrypoint") and str(svc["entrypoint"][0]).startswith("factorlab-")
    )


def _referenced_scripts() -> list[str]:
    found: set[str] = set()
    for caller in CALLERS:
        if caller.is_file():
            found.update(SCRIPT_REF.findall(caller.read_text(encoding="utf-8")))
    return sorted(found)


def test_every_factorlab_service_starts_a_component_entry_point():
    started = {name for name, _ in _compose_entrypoints()}
    assert {
        "api",
        "bootstrap",
        "ingest-india",
        "ingest-us",
        "universe-us",
        "ingest-political",
        "ibkr-snapshot",
        "cloudflare-secrets-agent",
    } <= started


@pytest.mark.parametrize(("service", "entrypoint"), _compose_entrypoints())
def test_compose_entry_point_is_a_declared_console_script(service, entrypoint):
    scripts = _console_scripts()
    assert entrypoint in scripts, f"{service}: {entrypoint} is not a [project.scripts] entry"
    module, _, attribute = scripts[entrypoint].partition(":")
    assert callable(getattr(importlib.import_module(module), attribute))


@pytest.mark.parametrize("script", _referenced_scripts() or ["<none>"])
def test_referenced_script_forwards_to_importable_code(script):
    if script == "<none>":
        pytest.skip("no scripts/*.py path is referenced by deploy files")
    path = REPO / script
    assert path.is_file(), f"{script} is invoked by production but missing"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets = [
        (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("factorlab.")
        for alias in node.names
    ]
    assert targets, f"{script} should forward to a factorlab module"
    for module, name in targets:
        assert callable(getattr(importlib.import_module(module), name))
