"""tools/scaffold.py: templates render into members the workspace checks accept.

Offline and fast (--root on a copy, --no-sync). The full path, `uv lock`, `uv sync`
and the new member's tests, is what the command runs by default.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    key = f"{name}_tool"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, REPO / "tools" / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


scaffold = _load("scaffold")
components = _load("components")
check_docs = _load("check_docs")


@pytest.fixture
def root(tmp_path):
    copy = tmp_path / "repo"
    for relative in (
        "pyproject.toml",
        "tests/architecture/test_boundaries.py",
        "deploy/scripts/prepare-host.sh",
        "deploy/logrotate/factorlab",
        "configs/ingestion/bindings.yaml",
    ):
        (copy / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / relative, copy / relative)
    shutil.copytree(
        REPO / "components" / "ingest-political",
        copy / "components" / "ingest-political",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return copy


def run(root: Path, *argv: str) -> int:
    return scaffold.main([*argv, "--root", str(root), "--no-sync"])


def _compiles(member: Path) -> None:
    for path in member.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def _context_ok(member: Path) -> None:
    headings = [
        line
        for line in (member / "CONTEXT.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    assert headings == check_docs.context_headings()
    assert "CONTEXT.md" in (member / "CLAUDE.md").read_text(encoding="utf-8")
    assert "CONTEXT.md" in (member / "AGENTS.md").read_text(encoding="utf-8")


def test_new_component_renders_a_valid_manifest_and_wires_the_host(root):
    assert run(root, "new-component", "ingest-crypto", "--writer", "--summary", "Crypto bars.") == 0
    member = root / "components" / "ingest-crypto"
    manifest = components.Component.model_validate(
        yaml.safe_load((member / "component.yaml").read_text(encoding="utf-8"))
    )
    assert (manifest.platform.rollback, manifest.platform.data_contract) == ("writer", 1)
    assert manifest.platform.image_var == "FACTORLAB_INGEST_CRYPTO_IMAGE"
    project = tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == {
        "factorlab-ingest-crypto": "factorlab.components.ingest_crypto.cli:main"
    }
    fragment = yaml.safe_load((member / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    assert list(fragment["services"]) == ["ingest-crypto"]
    assert "profiles" not in fragment["services"]["ingest-crypto"]
    assert (member / "src/factorlab/components/ingest_crypto/py.typed").exists()
    _compiles(member)
    _context_ok(member)
    root_sources = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "factorlab-component-ingest-crypto" in root_sources["tool"]["uv"]["sources"]
    assert "ingest-crypto" in (root / "deploy/scripts/prepare-host.sh").read_text(encoding="utf-8")
    assert "/var/log/factorlab/ingest-crypto/*.jsonl" in (
        root / "deploy/logrotate/factorlab"
    ).read_text(encoding="utf-8")


def test_new_provider_wires_its_component_and_starts_as_shadow(root):
    assert (
        run(
            root,
            "new-provider",
            "coinbase",
            "--dataset",
            "market.bars",
            "--market",
            "USA",
            "--resolutions",
            "1min",
            "--component",
            "ingest-political",
            "--bind",
        )
        == 0
    )
    member = root / "providers" / "coinbase"
    _compiles(member)
    _context_ok(member)
    sources = (member / "src/factorlab/sources/coinbase/sources.py").read_text(encoding="utf-8")
    assert 'resolutions=frozenset({"1min"})' in sources
    component = root / "components" / "ingest-political"
    assert '"coinbase"' in (
        component / "src/factorlab/components/ingest_political/providers.py"
    ).read_text("utf-8")
    manifest = yaml.safe_load((component / "component.yaml").read_text(encoding="utf-8"))
    assert "coinbase" in manifest["providers"]
    project = tomllib.loads((component / "pyproject.toml").read_text(encoding="utf-8"))
    assert "factorlab-provider-coinbase" in project["project"]["dependencies"]
    bindings = yaml.safe_load((root / "configs/ingestion/bindings.yaml").read_text("utf-8"))
    [binding] = [b for b in bindings["bindings"] if b["provider"] == "coinbase"]
    assert binding["role"] == "shadow"
    assert '    "coinbase",\n' in (root / "tests/architecture/test_boundaries.py").read_text(
        "utf-8"
    )


def test_new_lib_gets_its_layer(root):
    assert run(root, "new-lib", "features", "--depends-on", "ingest") == 0
    text = (root / "tests/architecture/test_boundaries.py").read_text(encoding="utf-8")
    assert '"features": {"calendars", "core", "ingest"},' in text
    _compiles(root / "libs" / "features")


@pytest.mark.parametrize(
    "argv",
    [
        ["new-component", "Bad_Name"],
        ["new-provider", "x-y", "--dataset", "no.such", "--market", "USA"],
        [
            "new-provider",
            "coinbase",
            "--dataset",
            "ref.listings",
            "--market",
            "USA",
            "--resolutions",
            "1min",
        ],
        ["new-lib", "features", "--depends-on", "nope"],
    ],
)
def test_bad_requests_change_nothing(root, argv, capsys):
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert run(root, *argv) == 1
    assert "error:" in capsys.readouterr().err
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_an_existing_member_is_never_overwritten(root):
    assert run(root, "new-component", "ingest-crypto") == 0
    assert run(root, "new-component", "ingest-crypto") == 1
