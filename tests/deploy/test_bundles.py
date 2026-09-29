"""Deploy bundles (tools/components.py bundle) are what the host deployer accepts."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # pydantic resolves annotations through sys.modules
    spec.loader.exec_module(module)
    return module


components = _load("components_tool", REPO / "tools" / "components.py")
fd = _load("factorlab_deploy", REPO / "deploy" / "host" / "factorlab_deploy.py")
IMAGE = "ghcr.io/arjundoshi221/factorlab-{}@sha256:" + "e" * 64


@pytest.fixture(scope="module")
def manifests():
    return components.load_all()


@pytest.mark.parametrize(
    "name", [p.parent.name for p in sorted((REPO / "components").glob("*/component.yaml"))]
)
def test_every_component_bundle_passes_the_deployers_validation(tmp_path, manifests, name):
    out = tmp_path / f"{name}.tgz"
    version = components.build_bundle(name, out, manifests)
    staged = tmp_path / "staged"
    staged.mkdir()
    fd.extract_bundle(out, staged)
    release = fd.load_release(name, version, IMAGE.format(name), staged)
    assert (
        release.manifest["platform"]["image_var"]
        == f"FACTORLAB_{name.upper().replace('-', '_')}_IMAGE"
    )
    assert [s["name"] for s in release.services]


def test_a_bundle_for_the_wrong_version_is_refused(tmp_path, manifests):
    with pytest.raises(ValueError, match="in the source tree"):
        components.build_bundle("api", tmp_path / "api.tgz", manifests, expected="9.9.9")


def test_platform_bundle_is_reproducible_executable_lf_and_complete(tmp_path, manifests):
    first, second = tmp_path / "a.tgz", tmp_path / "b.tgz"
    components.build_bundle("platform", first, manifests)
    components.build_bundle("platform", second, manifests)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        members = {m.name: m for m in archive.getmembers()}
        for required in (
            "deploy/compose.base.yml",
            "deploy/host/factorlab_deploy.py",
            "deploy/scripts/prepare-host.sh",
            "deploy/scripts/deploy-component.sh",
            "deploy/scripts/deploy-platform.sh",
            "deploy/scripts/factorlab-compose",
            "deploy/host/factorlab_log_reader.py",
            "deploy/ssh/60-factorlab-logs.conf",
        ):
            assert required in members, required
        for name in ("deploy/scripts/deploy-component.sh", "deploy/host/factorlab_deploy.py"):
            assert members[name].mode == 0o755
        assert members["deploy/compose.base.yml"].mode == 0o644
        assert not [n for n in members if "__pycache__" in n or n.endswith("production.env")]
        for name in members:
            if (
                name.endswith((".sh", ".yml", ".yaml"))
                or name == "deploy/scripts/factorlab-compose"
            ):
                assert b"\r\n" not in archive.extractfile(name).read(), name
        seeded = {n.split("/")[1] for n in members if n.startswith("components/")}
        assert seeded == {c.name for c in manifests}
        api = json.loads(archive.extractfile("components/api/component.json").read())
        assert api["version"] == components.version_of(
            next(c for c in manifests if c.name == "api")
        )


def test_platform_version_is_declared(manifests):
    assert components.VERSION.match(components.load_platform().version)


BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
@pytest.mark.parametrize(
    "script",
    [
        "deploy-component.sh",
        "deploy-platform.sh",
        "factorlab-compose",
        "deploy-release.sh",
        "rollback-release.sh",
    ],
)
def test_host_scripts_parse(script):
    source = (REPO / "deploy" / "scripts" / script).read_bytes().replace(b"\r\n", b"\n")
    result = subprocess.run([BASH, "-n"], input=source, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], "usage:"),
        (["api", "1.0.0"], "usage:"),
        (["rollback"], "usage:"),
        (["rollback", "api", "1.0.0", "extra"], "usage:"),
    ],
)
def test_deploy_component_wrapper_rejects_bad_arguments(argv, expected):
    script = REPO / "deploy" / "scripts" / "deploy-component.sh"
    source = script.read_bytes().replace(b"\r\n", b"\n").decode()
    result = subprocess.run(
        [BASH, "-c", source, "deploy-component.sh", *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and expected in result.stderr


@pytest.mark.parametrize("script", ["deploy-release.sh", "rollback-release.sh"])
def test_legacy_monolith_scripts_refuse_a_per_component_host(script):
    text = (REPO / "deploy" / "scripts" / script).read_text(encoding="utf-8")
    guard = text.index("if [[ -e $root/state/images.env ]]; then")
    # The guard runs before the script takes the lock or stages anything.
    assert guard < text.index("exec 9>")
    assert "mktemp" not in text[:guard]
