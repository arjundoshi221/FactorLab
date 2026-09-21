"""Pre-commit hook: refuse to stage files that contain plaintext secrets.

Greps each staged file for query-string secret patterns:
  api_key=<long_value>
  token=<long_value>
  secret=<long_value>
  access_token=<long_value>

Allowed: any value already redacted (`...=REDACTED`) or sentinel values
(`<your_key_here>`, etc.).

Skips: binary files, files larger than 1MB (probably aren't human-edited).

Receives staged file paths via argv. Exits 1 on any unredacted secret.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns that look like a secret embedded in a URL or assignment
PAT = re.compile(
    r"(?P<key>api_key|apikey|token|access_token|secret|auth)"
    r"\s*[=:]\s*"
    r"(?P<val>['\"]?[A-Za-z0-9_\-]{15,}['\"]?)",
    re.IGNORECASE,
)

REDACTED_MARKERS = ("REDACTED", "<your_", "<YOUR_", "your-key-here",
                    "xxx", "XXX", "...", "***")


def is_safe(value: str) -> bool:
    return any(m in value for m in REDACTED_MARKERS)


def scan(path: Path) -> list[tuple[int, str]]:
    if not path.exists() or not path.is_file():
        return []
    if path.stat().st_size > 1_000_000:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return []  # binary or unreadable — skip
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        for m in PAT.finditer(line):
            val = m.group("val")
            if not is_safe(val):
                hits.append((ln, line.strip()[:160]))
    return hits


def main() -> int:
    blocked = False
    for p in sys.argv[1:]:
        for ln, line in scan(Path(p)):
            if not blocked:
                print("ERROR: pre-commit blocked plaintext-secret patterns:",
                      file=sys.stderr)
            blocked = True
            print(f"  {p}:{ln}: {line}", file=sys.stderr)
    if blocked:
        print(file=sys.stderr)
        print("If this is a false positive (test fixture, documentation example),",
              file=sys.stderr)
        print("replace the value with REDACTED, <your_key_here>, or similar.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
