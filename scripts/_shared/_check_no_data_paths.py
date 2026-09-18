"""Pre-commit hook: refuse to stage paths under data/, logs/, or .env.

Defense in depth on top of .gitignore. .gitignore can be defeated with
`git add -f` or by editing .gitignore in the same commit; this hook runs
on every commit and refuses outright.

Allowed exceptions: anything under tests/fixtures/ or docs/ (small curated
samples that legitimately ship in the repo).

Receives staged file paths via argv (pre-commit framework convention).
Exits 1 (blocking) on any banned path.
"""

from __future__ import annotations

import sys

BANNED_PREFIXES = ("data/", "logs/", "data\\", "logs\\")
BANNED_EXACT = (".env",)
ALLOWED_PREFIXES = ("tests/fixtures/", "docs/")


def is_banned(path: str) -> bool:
    p = path.replace("\\", "/")
    if any(p.startswith(a) for a in ALLOWED_PREFIXES):
        return False
    if p in BANNED_EXACT or p.startswith(".env."):
        return True
    return any(p.startswith(b.replace("\\", "/")) for b in BANNED_PREFIXES)


def main() -> int:
    bad = [p for p in sys.argv[1:] if is_banned(p)]
    if not bad:
        return 0
    print("ERROR: pre-commit blocked staging of data / logs / secrets paths:",
          file=sys.stderr)
    for p in bad:
        print(f"  - {p}", file=sys.stderr)
    print(file=sys.stderr)
    print("Allowed exceptions: tests/fixtures/**, docs/**", file=sys.stderr)
    print("If you genuinely need to commit one of these, do an explicit "
          "`git add -f <path>` only after rotating any potentially-leaked "
          "credentials.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
