"""CI guard: scan src/factorlab/sources/ibkr/ for order-mutation patterns.

The IBKR adapter is read-only by contract (docs/data-sources/06-ibkr.md §4).
This test scans every .py under the module tree and fails if any known
order-placement / cancellation / modification symbol appears.

If you legitimately need to import an Order subclass (you don't — this
codebase is read-only), that work belongs in a separate trade-engine repo,
not here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[3] / "src" / "factorlab" / "sources" / "ibkr"

# Substring patterns (case-sensitive) — any hit fails the test.
FORBIDDEN_SUBSTRINGS = [
    "placeOrder(",
    "cancelOrder(",
    "modifyOrder(",
    "MarketOrder",
    "LimitOrder",
    "StopOrder",
    "StopLimitOrder",
    "BracketOrder",
    "MidPriceOrder",
    "from ib_async.order",
]

# Regex patterns
FORBIDDEN_REGEX = [
    re.compile(r"ib_async\s+import.*Order"),
]


def _iter_source_files() -> list[Path]:
    return sorted(p for p in MODULE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_module_tree_exists():
    assert MODULE_ROOT.is_dir(), f"expected {MODULE_ROOT} to exist"
    assert _iter_source_files(), "no .py files found under IBKR module — this test would false-pass"


@pytest.mark.parametrize("path", _iter_source_files(), ids=lambda p: str(p.relative_to(MODULE_ROOT)))
def test_no_forbidden_symbols(path: Path):
    text = path.read_text(encoding="utf-8")
    hits: list[str] = []
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle in text:
            hits.append(needle)
    for pat in FORBIDDEN_REGEX:
        m = pat.search(text)
        if m:
            hits.append(f"regex:{pat.pattern} -> {m.group(0)!r}")
    assert not hits, (
        f"{path.relative_to(MODULE_ROOT)} contains forbidden order-mutation symbols:\n"
        + "\n".join(f"  - {h}" for h in hits)
        + "\nThe IBKR adapter is read-only. Move any order-placement code to the trade-engine repo."
    )


def test_scan_covers_every_module_file():
    """Sanity: prove the parametrization actually enumerates files.

    Prints the scanned set to test output (visible with -v) so 'zero hits'
    can be audited: the substring list above ran against these files.
    """
    files = [str(p.relative_to(MODULE_ROOT)) for p in _iter_source_files()]
    # Adapter must have grown all the files listed in the spec.
    expected = {
        "__init__.py", "client.py", "shapes.py", "contracts.py",
        "portfolio.py", "executions.py", "open_orders.py",
        "historical.py", "pacing.py", "errors.py",
    }
    missing = expected - set(files)
    assert not missing, f"IBKR module missing expected files: {missing}"
