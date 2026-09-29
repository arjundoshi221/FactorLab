"""Which release units a change affects, from the workspace dependency graph.

    uv run python tools/affected.py [--base REF] [--head REF] [--json]
    uv run python tools/affected.py closure <unit>

A release unit is a deployable component (components/*/component.yaml), the platform
(deploy/) or a Cloudflare Worker (cloudflare/*/package.json). A Python component's
closure is its own directory, every workspace library and provider it depends on
(transitively), the configs its image copies, and the third-party packages uv.lock
resolves for it. Tests, CONTEXT and CHANGELOG files never make a release necessary.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parents[1]
# Paths inside a closure that do not change what ships.
NOT_SHIPPED = ("/tests/", "/CHANGELOG.md", "/CONTEXT.md", "/AGENTS.md", "/CLAUDE.md")


def _load_tool(name: str):
    """tools/ is a directory of scripts, not a package: load a sibling by path, once."""
    key = f"{name}_tool"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module  # pydantic resolves annotations through sys.modules
        spec.loader.exec_module(module)
    return sys.modules[key]


@dataclass(frozen=True)
class Unit:
    """Something released on its own ``<name>/vX.Y.Z`` tag."""

    name: str
    kind: Literal["python", "static", "platform", "worker"]
    root: str                      # repository-relative directory
    package: str | None = None     # Python distribution name
    paths: tuple[str, ...] = field(default=())  # closure path prefixes (directories end in /)

    @property
    def tag_prefix(self) -> str:
        return f"{self.name}/v"


def members() -> dict[str, str]:
    """Workspace distribution name -> repository-relative member directory."""
    found = {}
    for pattern in ("libs/*/pyproject.toml", "providers/*/pyproject.toml",
                    "components/*/pyproject.toml"):
        for path in sorted(REPO.glob(pattern)):
            project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
            found[project["name"]] = path.parent.relative_to(REPO).as_posix()
    return found


def _requirement_name(requirement: str) -> str:
    for stop in "<>=!~;[ ":
        requirement = requirement.split(stop, 1)[0]
    return requirement.strip().lower().replace("_", "-")


def workspace_closure(package: str) -> list[str]:
    """``package`` and every workspace member it depends on, transitively."""
    directories = members()
    seen, pending = [], [package]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.append(name)
        pyproject = REPO / directories[name] / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        pending += [dep for dep in map(_requirement_name, project.get("dependencies", []))
                    if dep in directories]
    return sorted(seen)


def units() -> list[Unit]:
    components = _load_tool("components")
    found = []
    directories = members()
    for c in components.load_all():
        root = f"components/{c.name}/"
        if c.kind == "static":
            found.append(Unit(c.name, "static", root, paths=(root,)))
            continue
        paths = [f"{directories[m]}/" for m in workspace_closure(c.package)]
        dockerfile = (REPO / c.dockerfile).read_text(encoding="utf-8")
        if "COPY configs " in dockerfile:
            paths.append("configs/")
        found.append(Unit(c.name, "python", root, c.package, tuple(sorted(paths))))
    found.append(Unit("platform", "platform", "deploy/", paths=("deploy/",)))
    for package_json in sorted(REPO.glob("cloudflare/*/package.json")):
        root = f"{package_json.parent.relative_to(REPO).as_posix()}/"
        found.append(Unit(package_json.parent.name, "worker", root, paths=(root,)))
    return found


def unit(name: str) -> Unit:
    for candidate in units():
        if candidate.name == name:
            return candidate
    raise KeyError(f"unknown release unit {name}")


def ships(unit_: Unit, path: str) -> bool:
    """Whether a changed repository path is part of what ``unit_`` releases."""
    return (any(path.startswith(prefix) for prefix in unit_.paths)
            and not any(marker in f"/{path}" for marker in NOT_SHIPPED))


# ── uv.lock: third-party versions per package ────────────────────────────────

def lock_packages(text: str) -> dict[str, dict]:
    return {p["name"]: p for p in tomllib.loads(text).get("package", [])} if text else {}


def third_party(lock: dict[str, dict], package: str) -> dict[str, str]:
    """Resolved third-party name -> version for ``package`` (markers ignored: a superset)."""
    resolved, pending, seen = {}, [package], set()
    while pending:
        name = pending.pop()
        if name in seen or name not in lock:
            continue
        seen.add(name)
        entry = lock[name]
        source = entry.get("source", {})
        if "editable" not in source and "virtual" not in source:
            resolved[name] = entry.get("version", "")
        pending += [dep["name"] for dep in entry.get("dependencies", [])]
    return resolved


def lock_changes(unit_: Unit, base: str, head: str | None = None) -> list[str]:
    """``name old -> new`` lines for third-party packages that changed in ``unit_``'s image."""
    if unit_.kind != "python":
        return []
    old = lock_packages(_git_show(base, "uv.lock"))
    new = lock_packages(_git_show(head, "uv.lock") if head else
                        (REPO / "uv.lock").read_text(encoding="utf-8"))
    before, after = third_party(old, unit_.package), third_party(new, unit_.package)
    return [f"{name} {before.get(name, '(new)')} -> {after.get(name, '(removed)')}"
            for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]


# ── git ──────────────────────────────────────────────────────────────────────

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def _git_show(ref: str, path: str) -> str:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO, capture_output=True,
                            text=True, encoding="utf-8", check=False)
    return result.stdout if result.returncode == 0 else ""


def changed_paths(base: str, head: str | None = None) -> list[str]:
    """Files changed between ``base`` and ``head`` (default: the working tree)."""
    args = ["diff", "--name-only", "--no-renames", base] + ([head] if head else [])
    return sorted({p for p in _git(*args).splitlines() if p})


def affected(base: str, head: str | None = None) -> dict[str, list[str]]:
    """Release unit -> the reasons it changed (paths, then third-party lock updates)."""
    paths = changed_paths(base, head)
    result = {}
    for candidate in units():
        reasons = [p for p in paths if ships(candidate, p)]
        if "uv.lock" in paths:
            reasons += [f"uv.lock: {line}" for line in lock_changes(candidate, base, head)]
        if reasons:
            result[candidate.name] = reasons
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="affected.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", nargs="?", default="affected", choices=["affected", "closure"])
    parser.add_argument("unit", nargs="?")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "closure":
        if not args.unit:
            parser.error("closure needs a unit name")
        chosen = unit(args.unit)
        print("\n".join(chosen.paths + (("uv.lock (third-party)",) if chosen.package else ())))
        return 0
    result = affected(args.base, args.head)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for name, reasons in result.items():
            print(f"{name}: {len(reasons)} change(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
