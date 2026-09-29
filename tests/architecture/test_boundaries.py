"""Layer boundaries for ingestion (docs/architecture/07 §3.1, §14).

R1  factorlab.sources.* never imports factorlab.storage.*
R2  factorlab.storage.* never imports factorlab.sources.* or factorlab.components.*
R3  factorlab/storage/ holds no provider-name string literals
R4  the engine layer (factorlab.ingest, factorlab.orchestration) and engine-driven
    scripts never import a concrete provider or a component
R5  libraries (everything outside factorlab.components) never import a component
R6  components never import each other (each ships as its own image)

Today's violations live in ``boundary_allowlist.txt`` as ``rule path token count``.
The list is a ratchet: a violation that is not listed fails, and so does a
listed violation whose count dropped, so every migration phase must shrink it.
Regenerate after a real reduction with::

    python tests/architecture/test_boundaries.py > tests/architecture/boundary_allowlist.txt
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # run as a script to regenerate the allowlist
from _workspace import REPO, module_name, package_roots, python_files

ALLOWLIST = Path(__file__).with_name("boundary_allowlist.txt")

# Provider names as they appear in `source` columns, and vendor-only codes.
PROVIDER_TOKENS = (
    "upstox", "schwab", "eodhd", "ibkr", "house_clerk", "congress_legislators", "edgar",
    "senate_efd", "senate_stock_watcher", "fec", "finnhub", "congress_gov", "lda",
    "usaspending",
)
VENDOR_LITERALS = frozenset({"NSE_EQ", "NSE_FO", "BSE_EQ", "BSE_FO", "NSE_INDEX"})
# The one sanctioned home for vendor alias kinds in storage (07 §8.4).
EXEMPT_ASSIGNMENTS = frozenset({"KNOWN_ALIAS_KINDS"})
# Scripts that run through the engine; they may not import providers (R4).
ENGINE_SCRIPTS = ("scripts/factlab_ingest.py",)


def _imports(path: Path, tree: ast.AST) -> list[set[str]]:
    """Candidate module names per import statement (``from a import b`` -> {a, a.b})."""
    in_src = any(path.is_relative_to(root) for root in package_roots())
    package = ""
    if in_src:
        name = module_name(path)
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    statements: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            statements += [{alias.name} for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            statements.append({module, *(f"{module}.{alias.name}" for alias in node.names)})
    return statements


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _exempt_constants(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in EXEMPT_ASSIGNMENTS for t in targets):
                ids |= {id(sub) for sub in ast.walk(node.value)} if node.value else set()
    return ids


def _provider_token(value: str) -> str | None:
    if value in VENDOR_LITERALS:
        return value
    for token in PROVIDER_TOKENS:
        if value == token or value.startswith((f"{token}_", f"{token}:")):
            return token
        if f"'{token}'" in value or f"'{token}_" in value:  # quoted inside SQL
            return token
    return None


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def violations() -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()

    def record(rule: str, path: Path, token: str) -> None:
        found[(rule, path.relative_to(REPO).as_posix(), token)] += 1

    for path, rel in python_files():
        tree = _parse(path)
        for statement in _imports(path, tree):
            tokens: set[tuple[str, str]] = set()
            for module in statement:
                top = ".".join(module.split(".")[:3])
                if rel.startswith("sources/") and module.startswith("factorlab.storage"):
                    tokens.add(("R1", top))
                if rel.startswith("storage/") and module.startswith(
                        ("factorlab.sources.", "factorlab.components.")):
                    tokens.add(("R2", top))
                if rel.startswith(("ingest/", "orchestration/")) and module.startswith(
                        ("factorlab.sources.", "factorlab.components.")):
                    tokens.add(("R4", top))
                if not rel.startswith("components/") and module.startswith(
                        "factorlab.components."):
                    tokens.add(("R5", top))
                if (rel.startswith("components/") and module.startswith("factorlab.components.")
                        and module.split(".")[2] != rel.split("/")[1]):
                    tokens.add(("R6", top))
            for rule, token in tokens:
                record(rule, path, token)
        if rel.startswith("storage/"):
            skip = _docstring_nodes(tree) | _exempt_constants(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in skip):
                    token = _provider_token(node.value)
                    if token:
                        record("R3", path, token)
    for script in ENGINE_SCRIPTS:
        path = REPO / script
        if not path.exists():
            continue
        for statement in _imports(path, _parse(path)):
            tokens = {".".join(module.split(".")[:3]) for module in statement
                      if module.startswith("factorlab.sources.")}
            for token in tokens:
                record("R4", path, token)
    return found


def _render(counter: Counter[tuple[str, str, str]]) -> str:
    return "".join(f"{rule} {path} {token} {count}\n"
                   for (rule, path, token), count in sorted(counter.items()))


def _load_allowlist() -> Counter[tuple[str, str, str]]:
    allowed: Counter[tuple[str, str, str]] = Counter()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rule, path, token, count = line.split()
        allowed[(rule, path, token)] = int(count)
    return allowed


def test_no_new_boundary_violations():
    current = violations()
    allowed = _load_allowlist()
    new = {key: n for key, n in current.items() if n > allowed.get(key, 0)}
    assert not new, "new layer-boundary violations (07 §3.1):\n" + _render(Counter(new))


def test_allowlist_only_shrinks():
    current = violations()
    allowed = _load_allowlist()
    stale = {key: n for key, n in allowed.items() if current.get(key, 0) < n}
    assert not stale, (
        "violations were removed; lower these allowlist entries "
        "(regenerate with `python tests/architecture/test_boundaries.py`):\n"
        + _render(Counter(stale))
    )


def test_new_ingestion_layers_are_clean():
    """The engine layer and the new sink package start, and must stay, violation-free."""
    current = violations()
    dirty = [key for key in current
             if key[1].startswith(("libs/ingest/", "libs/orchestration/",
                                   "libs/storage/src/factorlab/storage/sinks/"))]
    assert not dirty, dirty


if __name__ == "__main__":
    sys.stdout.write("# rule path token count - see test_boundaries.py; shrink-only.\n")
    sys.stdout.write(_render(violations()))
