"""tools/affected.py and tools/release.py: closures, version bumps, changelogs, tag checks."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    key = f"{name}_tool"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, REPO / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


affected = _load("affected")
release = _load("release")


# ── the real workspace ───────────────────────────────────────────────────────

def test_every_component_platform_and_worker_is_a_release_unit():
    names = {u.name for u in affected.units()}
    components = {p.parent.name for p in (REPO / "components").glob("*/component.yaml")}
    workers = {p.parent.name for p in (REPO / "cloudflare").glob("*/package.json")}
    assert names == components | workers | {"platform"}


def test_python_closures_follow_declared_workspace_dependencies():
    india, migrator = affected.unit("ingest-india"), affected.unit("schema-migrator")
    assert "components/ingest-india/" in india.paths and "providers/upstox/" in india.paths
    assert "providers/schwab/" not in india.paths
    assert not [p for p in migrator.paths if p.startswith("providers/")]


def test_tests_and_context_files_never_require_a_release():
    api = affected.unit("api")
    assert affected.ships(api, "components/api/src/factorlab/components/api/app.py")
    assert not affected.ships(api, "components/api/tests/test_app.py")
    assert not affected.ships(api, "components/api/CHANGELOG.md")
    assert not affected.ships(api, "docs/architecture/07-provider-abstraction.md")


def test_lock_closure_excludes_what_an_image_must_not_contain():
    lock = affected.lock_packages((REPO / "uv.lock").read_text(encoding="utf-8"))
    assert "pandas" not in affected.third_party(lock, "factorlab-component-schema-migrator")
    assert "ib-async" not in affected.third_party(lock, "factorlab-component-ingest-india")
    assert "fastapi" in affected.third_party(lock, "factorlab-component-api")


def test_check_tag_accepts_the_declared_version_and_rejects_anything_else():
    version = release.read_version(affected.unit("api"))
    outputs = release.check_tag(f"api/v{version}")
    assert outputs["image"] == "ghcr.io/arjundoshi221/factorlab-api"
    assert outputs["dockerfile"] == "components/api/Dockerfile"
    assert release.check_tag(f"platform/v{release.read_version(affected.unit('platform'))}")[
        "kind"] == "platform"
    for bad in ("api/v99.0.0", "api/1.0.0", "release/20260101T000000Z-abcdef1", "nope/v1.0.0"):
        with pytest.raises(release.ReleaseError):
            release.check_tag(bad)


@pytest.mark.parametrize(("version", "part", "expected"), [
    ("1.2.3", "patch", "1.2.4"), ("1.2.3", "minor", "1.3.0"), ("1.2.3", "major", "2.0.0"),
    ("0.1.0", "major", "1.0.0"),
])
def test_bump(version, part, expected):
    assert release.bump(version, part) == expected


def test_pre_releases_are_refused_for_now():
    with pytest.raises(release.ReleaseError):
        release.bump("1.0.0-rc.1", "patch")


@pytest.mark.parametrize(("subject", "body", "section"), [
    ("feat(api): add x", "", "feat"),
    ("fix: y", "", "fix"),
    ("perf(storage): z", "", "perf"),
    ("refactor!: drop v1", "", "breaking"),
    ("fix: a", "BREAKING CHANGE: b", "breaking"),
    ("deploy(host): c", "", "other"),
    ("Merge-less plain subject", "", "other"),
    ("chore(release): api v1.0.0", "", None),
    ("docs: typo", "", None),
    ("test: more", "", None),
])
def test_commit_sections(subject, body, section):
    assert release.Commit("0" * 40, subject, body, []).section() == section


# ── a synthetic repository ───────────────────────────────────────────────────

LOCK = """version = 1

[[package]]
name = "factorlab-component-x"
version = "0.1.0"
source = {{ editable = "components/x" }}
dependencies = [{{ name = "factorlab-y" }}, {{ name = "pandas" }}]

[[package]]
name = "factorlab-y"
version = "0.1.0"
source = {{ editable = "libs/y" }}

[[package]]
name = "pandas"
version = "{pandas}"
source = {{ registry = "https://pypi.org/simple" }}
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                              cwd=root, check=True, capture_output=True, text=True).stdout

    def commit(message, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        git("add", "-A")
        git("commit", "-q", "-m", message)

    git("init", "-q")
    commit("chore: start", {
        "components/x/pyproject.toml": '[project]\nname = "factorlab-component-x"\n'
                                       'version = "0.1.0"\n',
        "components/x/src/a.py": "a = 1\n", "libs/y/src/b.py": "b = 1\n",
        "uv.lock": LOCK.format(pandas="2.2.1")})
    git("tag", "cx/v0.1.0")
    commit("feat(x): a new thing", {"components/x/src/a.py": "a = 2\n"})
    commit("fix(y): a library fix", {"libs/y/src/b.py": "b = 2\n"})
    commit("test(x): tests only", {"components/x/tests/test_a.py": "def test(): pass\n"})
    commit("docs: unrelated", {"docs/readme.md": "hi\n"})
    commit("refactor(x)!: rename the thing", {"components/x/src/a.py": "renamed = 2\n"})
    commit("build(deps): bump pandas", {"uv.lock": LOCK.format(pandas="2.2.3")})
    unit = affected.Unit("cx", "python", "components/x/", "factorlab-component-x",
                         ("components/x/", "libs/y/"))
    monkeypatch.setattr(affected, "REPO", root)
    monkeypatch.setattr(release, "REPO", root)
    monkeypatch.setattr(affected, "unit", lambda name: unit)
    return root, unit, git, commit


def test_changelog_groups_own_changes_library_changes_and_dependencies(repo):
    _, unit, _, _ = repo
    section = release.changelog_section(unit, "0.2.0", "cx/v0.1.0", date(2026, 9, 29))
    assert section.startswith("## 0.2.0 - 2026-09-29\n")
    breaking = section.index("### Breaking changes")
    features = section.index("### Features")
    library = section.index("### Included library changes")
    dependencies = section.index("### Dependencies")
    assert breaking < features < library < dependencies
    assert "rename the thing" in section[breaking:features]
    assert "a new thing" in section[features:library]
    assert "fix(y): a library fix" in section[library:dependencies]
    assert "- pandas 2.2.1 -> 2.2.3" in section[dependencies:]
    assert "tests only" not in section and "unrelated" not in section


def test_prepare_bumps_writes_the_changelog_and_refuses_repeats(repo):
    root, _, git, commit = repo
    plan = release.prepare("cx", "minor", lock=False, today=date(2026, 9, 29))
    assert (plan["version"], plan["tag"], plan["previous"]) == ("0.2.0", "cx/v0.2.0", "cx/v0.1.0")
    pyproject = tomllib.loads((root / "components/x/pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.2.0"
    changelog = (root / "components/x/CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog: cx\n") and "## 0.2.0 - 2026-09-29" in changelog
    commit("chore(release): cx v0.2.0", {})
    git("tag", "cx/v0.2.0")
    with pytest.raises(release.ReleaseError, match="already exists"):
        release.prepare("cx", None, lock=False)
    with pytest.raises(release.ReleaseError, match="nothing cx ships changed"):
        release.prepare("cx", "patch", lock=False)
    commit("fix(x): after the release", {"components/x/src/a.py": "renamed = 3\n"})
    later = release.prepare("cx", "patch", lock=False, today=date(2026, 10, 1))
    assert later["version"] == "0.2.1"
    text = (root / "components/x/CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("## 0.2.1") < text.index("## 0.2.0")


def test_a_failed_prepare_restores_every_file(repo, monkeypatch):
    _, _, git, _ = repo
    before = git("status", "--porcelain")

    real_run = subprocess.run

    def failing_lock(command, *args, **kwargs):
        if command[:2] == ["uv", "lock"]:
            return subprocess.CompletedProcess(command, 1, "", "resolution failed")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", failing_lock)
    with pytest.raises(release.ReleaseError, match="uv lock failed"):
        release.prepare("cx", "minor", today=date(2026, 9, 29))
    assert git("status", "--porcelain") == before


def test_dry_run_changes_nothing(repo):
    _, _, git, _ = repo
    before = git("status", "--porcelain")
    plan = release.prepare("cx", "major", dry_run=True, lock=False)
    assert plan["version"] == "1.0.0" and plan["files"] == []
    assert git("status", "--porcelain") == before


def test_npm_versions_update_package_and_lock(tmp_path, monkeypatch):
    root = tmp_path / "npm"
    (root / "w").mkdir(parents=True)
    (root / "w" / "package.json").write_text(json.dumps(
        {"name": "w", "version": "0.1.0", "private": True}, indent=2) + "\n")
    (root / "w" / "package-lock.json").write_text(json.dumps(
        {"name": "w", "version": "0.1.0", "lockfileVersion": 3,
         "packages": {"": {"name": "w", "version": "0.1.0"}}}, indent=2) + "\n")
    monkeypatch.setattr(release, "REPO", root)
    unit = affected.Unit("w", "worker", "w/", paths=("w/",))
    release.write_version(unit, "0.2.0")
    lock = json.loads((root / "w" / "package-lock.json").read_text())
    assert (lock["version"], lock["packages"][""]["version"]) == ("0.2.0", "0.2.0")
    assert release.read_version(unit) == "0.2.0"


def test_platform_version_lives_in_deploy_component_yaml(tmp_path, monkeypatch):
    root = tmp_path / "p"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "component.yaml").write_text("schema: 1\nname: platform\nversion: 0.1.0\n")
    monkeypatch.setattr(release, "REPO", root)
    unit = affected.Unit("platform", "platform", "deploy/", paths=("deploy/",))
    release.write_version(unit, "1.0.0")
    assert "version: 1.0.0\n" in (root / "deploy" / "component.yaml").read_text()
