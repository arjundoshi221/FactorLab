"""Package-structure rules that keep every component installable and relocatable.

N1  ``factorlab``, ``factorlab.sources`` and ``factorlab.components`` are PEP 420
    namespaces: a regular ``__init__.py`` there would shadow every other
    distribution that contributes to the namespace once they ship separately.
N2  Product code never loads ``.env``, edits ``sys.path``, or locates files by
    walking up from ``__file__`` (a module may still read package data next to
    itself via ``Path(__file__).parent``). Locations come from
    ``factorlab.core.paths``; settings from ``factorlab.core.settings``.
N3  Legacy production paths (``*/legacy/``) may only shrink until each provider's
    engine cutover deletes them (docs/architecture/07 §15.2).
N4  Direct environment reads (``os.getenv`` / ``os.environ``) outside
    ``factorlab.core`` may only shrink: new configuration goes through typed
    settings.

Regenerate the ratchet files after a real reduction with::

    python tests/architecture/test_package_hygiene.py
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # run as a script to regenerate the lists
from _workspace import REPO, package_roots, python_files  # noqa: E402

NAMESPACES = ("", "sources", "components")
LEGACY_BUDGET = Path(__file__).with_name("legacy_budget.txt")
ENV_ALLOWLIST = Path(__file__).with_name("env_access_allowlist.txt")
ENV_OWNERS = ("core/",)


def _modules() -> list[Path]:
    return [path for path, _ in python_files()]


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _file_walk(node: ast.AST) -> bool:
    """``Path(__file__)...parents[n]`` or ``...parent.parent``."""
    text = ast.unparse(node)
    return "__file__" in text and ("parents[" in text or ".parent.parent" in text)


def hygiene_violations() -> list[str]:
    found = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "dotenv"
                                                    for a in node.names):
                found.append(f"{_rel(path)}:{node.lineno} imports dotenv")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "dotenv":
                found.append(f"{_rel(path)}:{node.lineno} imports dotenv")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and ast.unparse(node.func) in {"sys.path.insert", "sys.path.append"}):
                found.append(f"{_rel(path)}:{node.lineno} edits sys.path")
            elif isinstance(node, (ast.Subscript, ast.Attribute)) and _file_walk(node):
                found.append(f"{_rel(path)}:{node.lineno} walks up from __file__")
    return sorted(set(found))


def legacy_files() -> list[str]:
    return sorted(_rel(p) for p, rel in python_files() if "legacy" in rel.split("/"))


def env_reads() -> Counter[str]:
    counts: Counter[str] = Counter()
    for path, rel in python_files():
        if rel.startswith(ENV_OWNERS):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Attribute) and ast.unparse(node) in {"os.getenv", "os.environ"}:
                counts[_rel(path)] += 1
    return counts


def _read_list(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_namespace_packages_have_no_init():
    shadowing = [_rel(root / ns / "__init__.py") for root in package_roots() for ns in NAMESPACES
                 if (root / ns / "__init__.py").exists()]
    assert not shadowing, f"remove these to keep the namespaces open (N1): {shadowing}"


def test_no_dotenv_sys_path_or_file_walks_in_product_code():
    violations = hygiene_violations()
    assert not violations, "N2 violations:\n" + "\n".join(violations)


def test_legacy_code_only_shrinks():
    budget = set(_read_list(LEGACY_BUDGET))
    current = set(legacy_files())
    assert not current - budget, f"new legacy files are not allowed (N3): {sorted(current - budget)}"
    assert not budget - current, (
        f"legacy files were removed; drop them from {LEGACY_BUDGET.name}: {sorted(budget - current)}")


def test_direct_environment_reads_only_shrink():
    allowed = Counter({line.rsplit(" ", 1)[0]: int(line.rsplit(" ", 1)[1])
                       for line in _read_list(ENV_ALLOWLIST)})
    current = env_reads()
    grown = {f: n for f, n in current.items() if n > allowed.get(f, 0)}
    shrunk = {f: n for f, n in allowed.items() if current.get(f, 0) < n}
    assert not grown, f"read new configuration through typed settings instead (N4): {grown}"
    assert not shrunk, f"environment reads were removed; lower {ENV_ALLOWLIST.name}: {shrunk}"


if __name__ == "__main__":
    LEGACY_BUDGET.write_text(
        "# Legacy production files; shrink-only (see test_package_hygiene.py N3).\n"
        + "".join(f"{f}\n" for f in legacy_files()), encoding="utf-8")
    ENV_ALLOWLIST.write_text(
        "# Direct os.getenv/os.environ reads outside factorlab.core; shrink-only (N4).\n"
        + "".join(f"{f} {n}\n" for f, n in sorted(env_reads().items())), encoding="utf-8")
    sys.stdout.write(f"wrote {LEGACY_BUDGET.name} and {ENV_ALLOWLIST.name}\n")
