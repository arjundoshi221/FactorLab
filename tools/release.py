"""Version bumps, changelogs and tag checks for per-unit releases (``<unit>/vX.Y.Z``).

    uv run python tools/release.py prepare <unit> [--bump patch|minor|major] [--dry-run]
    uv run python tools/release.py check-tag <unit>/vX.Y.Z      (the release workflow's gate)
    uv run python tools/release.py last-tag <unit>

``prepare`` is what ``deploy/release.ps1 -Component`` runs on a clean, synchronized
main. It refuses a version that is already tagged and a release whose closure (see
tools/affected.py) has not changed since the unit's last tag. Otherwise it writes
the new version where the unit declares it and prepends a CHANGELOG.md section
built from the conventional commits that touched the closure. For Python units it
then runs ``uv lock``. It prints JSON: unit, version, tag, files and notes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TAG = re.compile(r"^(?P<unit>[a-z][a-z0-9-]{1,40})/v(?P<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\."
                 r"(0|[1-9]\d*))$")
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<subject>.+)$")
SECTIONS = (("breaking", "Breaking changes"), ("feat", "Features"), ("fix", "Fixes"),
            ("perf", "Performance"), ("other", "Other changes"))
QUIET_TYPES = {"docs", "test", "ci", "style"}


def _load_tool(name: str):
    """tools/ is a directory of scripts, not a package: load a sibling by path, once."""
    key = f"{name}_tool"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module  # pydantic resolves annotations through sys.modules
        spec.loader.exec_module(module)
    return sys.modules[key]


affected = _load_tool("affected")


class ReleaseError(Exception):
    pass


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                            encoding="utf-8", check=False)
    if check and result.returncode:
        raise ReleaseError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


# ── versions ─────────────────────────────────────────────────────────────────

def version_files(unit) -> list[Path]:
    """Where ``unit`` declares its version (the first file is authoritative)."""
    root = REPO / unit.root
    if unit.kind == "python":
        return [root / "pyproject.toml"]
    if unit.kind == "platform":
        return [root / "component.yaml"]
    return [path for path in (root / "package.json", root / "package-lock.json") if path.exists()]


def read_version(unit) -> str:
    primary = version_files(unit)[0]
    text = primary.read_text(encoding="utf-8")
    if unit.kind == "python":
        return tomllib.loads(text)["project"]["version"]
    if unit.kind == "platform":
        return re.search(r"(?m)^version: *(\S+) *$", text).group(1)
    return json.loads(text)["version"]


def write_version(unit, version: str) -> list[Path]:
    written = []
    for path in version_files(unit):
        text = path.read_text(encoding="utf-8")
        if path.name == "pyproject.toml":
            updated = re.sub(r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1)
        elif path.name == "component.yaml":
            updated = re.sub(r"(?m)^version: *\S+ *$", f"version: {version}", text, count=1)
        else:
            data = json.loads(text)
            data["version"] = version
            if path.name == "package-lock.json" and "" in data.get("packages", {}):
                data["packages"][""]["version"] = version
            updated = json.dumps(data, indent=2, ensure_ascii=False) + "\n"  # npm's format
        path.write_text(updated, encoding="utf-8", newline="\n")
        written.append(path)
    if read_version(unit) != version:
        raise ReleaseError(f"could not write version {version} for {unit.name}")
    return written


def bump(version: str, part: str) -> str:
    match = VERSION.match(version)
    if not match:
        raise ReleaseError(f"{version!r} is not X.Y.Z (pre-releases are not supported yet)")
    major, minor, patch = (int(g) for g in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"unknown bump {part!r}")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def tags(unit) -> list[str]:
    """The unit's release tags, newest version first."""
    found = [t for t in _git("tag", "--list", f"{unit.tag_prefix}*").split() if TAG.match(t)]
    return sorted(found, key=lambda t: _key(TAG.match(t)["version"]), reverse=True)


def last_tag(unit) -> str | None:
    found = tags(unit)
    return found[0] if found else None


# ── changelog ────────────────────────────────────────────────────────────────

@dataclass
class Commit:
    sha: str
    subject: str
    body: str
    files: list[str]

    def section(self) -> str | None:
        match = CONVENTIONAL.match(self.subject)
        if not match:
            return "other"
        if match["bang"] or "BREAKING CHANGE" in self.body:
            return "breaking"
        if match["type"] == "chore" and (match["scope"] or "") == "release":
            return None
        if match["type"] in QUIET_TYPES:
            return None
        return match["type"] if match["type"] in {"feat", "fix", "perf"} else "other"


def commits_since(unit, since: str) -> list[Commit]:
    """Non-merge commits after ``since`` that changed what ``unit`` ships."""
    log = _git("log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", f"{since}..HEAD", "--",
               *unit.paths, *(["uv.lock"] if unit.kind == "python" else []))
    commits = []
    for record in filter(None, (r.strip() for r in log.split("\x1e"))):
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        files = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).split()
        shipped = [f for f in files if affected.ships(unit, f) or f == "uv.lock"]
        if shipped:
            commits.append(Commit(sha, subject.strip(), body, shipped))
    return commits


def changelog_section(unit, version: str, since: str | None, today: date) -> str:
    lines = [f"## {version} - {today.isoformat()}", ""]
    if since is None:
        lines += ["First release as an independently released unit.", ""]
        return "\n".join(lines)
    own: dict[str, list[str]] = {}
    included: list[str] = []
    for commit in commits_since(unit, since):
        section = commit.section()
        if section is None:
            continue
        entry = f"- {commit.subject} ({commit.sha[:7]})"
        if any(f.startswith(unit.root) for f in commit.files):
            own.setdefault(section, []).append(entry)
        elif any(f != "uv.lock" for f in commit.files):
            included.append(entry)
    for key, title in SECTIONS:
        if own.get(key):
            lines += [f"### {title}", "", *own[key], ""]
    if included:
        lines += ["### Included library changes", "", *included, ""]
    dependencies = affected.lock_changes(unit, since)
    if dependencies:
        lines += ["### Dependencies", "", *(f"- {line}" for line in dependencies), ""]
    if len(lines) == 2:
        lines += ["Maintenance release.", ""]
    return "\n".join(lines)


def changelog_path(unit) -> Path:
    return REPO / unit.root / "CHANGELOG.md"


def changelog_header(unit) -> str:
    return (f"# Changelog: {unit.name}\n\nReleased as `{unit.tag_prefix}X.Y.Z` tags. Sections are "
            "generated by `tools/release.py prepare` from conventional commits; edit them before "
            "the release commit if needed.\n\n")


def prepend_changelog(unit, section: str) -> Path:
    path = changelog_path(unit)
    text = path.read_text(encoding="utf-8") if path.exists() else changelog_header(unit)
    head, marker, rest = text.partition("\n## ")
    updated = (head.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"
               + (f"\n## {rest}" if marker else ""))
    path.write_text(updated, encoding="utf-8", newline="\n")
    return path


# ── commands ─────────────────────────────────────────────────────────────────

def prepare(name: str, part: str | None, *, dry_run: bool = False, lock: bool = True,
            allow_unchanged: bool = False, today: date | None = None) -> dict:
    unit = affected.unit(name)
    current = read_version(unit)
    version = bump(current, part) if part else current
    if not VERSION.match(version):
        raise ReleaseError(f"{name} {version} is not X.Y.Z")
    tag = f"{unit.tag_prefix}{version}"
    existing = tags(unit)
    if tag in existing:
        raise ReleaseError(f"{tag} already exists; pass --bump to release a new version")
    since = existing[0] if existing else None
    if since and _key(version) <= _key(TAG.match(since)["version"]):
        raise ReleaseError(f"{version} is not newer than {since}")
    if since and not allow_unchanged:
        changed = [p for p in affected.changed_paths(since, "HEAD") if affected.ships(unit, p)]
        if not changed and not affected.lock_changes(unit, since, "HEAD"):
            raise ReleaseError(f"nothing {name} ships changed since {since}")
    section = changelog_section(unit, version, since, today or datetime.now(UTC).date())
    plan = {"unit": name, "kind": unit.kind, "version": version, "previous": since,
            "tag": tag, "changelog": section, "files": []}
    if dry_run:
        return plan
    touched = [*version_files(unit), changelog_path(unit)]
    if unit.kind == "python" and lock:
        touched.append(REPO / "uv.lock")
    originals = {path: path.read_bytes() if path.exists() else None for path in touched}
    try:
        write_version(unit, version)
        prepend_changelog(unit, section)
        if unit.kind == "python" and lock:
            locked = subprocess.run(["uv", "lock"], cwd=REPO, capture_output=True, text=True,
                                    check=False)
            if locked.returncode:
                raise ReleaseError(f"uv lock failed: {locked.stderr.strip()[-500:]}")
    except BaseException:
        # Leave main exactly as it was: a failed prepare must not strand a half release.
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        raise
    plan["files"] = [p.relative_to(REPO).as_posix() for p in touched]
    return plan


def check_tag(tag: str) -> dict[str, str]:
    """Outputs for the release workflow; raises unless the tag matches the checked-out tree."""
    match = TAG.match(tag)
    if not match:
        raise ReleaseError(f"{tag!r} is not <unit>/vX.Y.Z")
    try:
        unit = affected.unit(match["unit"])
    except KeyError as exc:
        raise ReleaseError(str(exc)) from None
    declared = read_version(unit)
    if declared != match["version"]:
        raise ReleaseError(f"{tag} does not match the tree: {unit.name} is {declared}")
    outputs = {"unit": unit.name, "version": declared, "kind": unit.kind}
    if unit.kind in {"python", "static"}:
        components = _load_tool("components")
        [component] = [c for c in components.load_all() if c.name == unit.name]
        outputs |= {"image": component.image, "dockerfile": component.dockerfile}
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("unit")
    prep.add_argument("--bump", choices=["patch", "minor", "major"])
    prep.add_argument("--dry-run", action="store_true")
    prep.add_argument("--no-lock", action="store_true")
    prep.add_argument("--allow-unchanged", action="store_true")
    check = commands.add_parser("check-tag")
    check.add_argument("tag")
    last = commands.add_parser("last-tag")
    last.add_argument("unit")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.unit, args.bump, dry_run=args.dry_run,
                                     lock=not args.no_lock,
                                     allow_unchanged=args.allow_unchanged), indent=2))
        elif args.command == "check-tag":
            for key, value in check_tag(args.tag).items():
                print(f"{key}={value}")
        else:
            print(last_tag(affected.unit(args.unit)) or "")
    except (ReleaseError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
