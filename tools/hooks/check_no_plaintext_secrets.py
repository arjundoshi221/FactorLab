"""Pre-commit hook: refuse to stage files that contain plaintext secrets.

Greps each staged file for secret-looking *values* (15+ characters):
  ...?api_token=<value> / &token=<value>         in a URL query string
  api_key = "<value>" / "token": '<value>'       a string literal assigned to a secret name
Variables and calls (``token = read_token_file()``) are not values and never match.

Allowed: any value already redacted (``REDACTED``) or a sentinel such as
``<your_key_here>``.

Skips: binary files, files larger than 1MB (probably aren't human-edited).

Receives staged file paths via argv. Exits 1 on any unredacted secret.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Secret-looking names, also as the tail of a longer one (FACTORLAB_API_KEY, api_token).
_KEY = r"(?:api_key|apikey|api_token|access_token|refresh_token|token|secret|password|auth)"
_VALUE = r"[A-Za-z0-9_\-.~+/]{15,}"
PATTERNS = (
    # a URL query string: ?api_token=abc... or &token=abc...
    re.compile(r"[?&]\w*" + _KEY + r"=(?P<val>" + _VALUE + r")", re.IGNORECASE),
    # a quoted literal assigned to a secret-named key: token = "abc...", "api_key": 'abc...'
    re.compile(
        r"\w*" + _KEY + r"""['"]?\s*[=:]\s*(?P<quote>['"])(?P<val>""" + _VALUE + r")(?P=quote)",
        re.IGNORECASE,
    ),
)

REDACTED_MARKERS = (
    "REDACTED",
    "redacted",
    "<your_",
    "<YOUR_",
    "your-key-here",
    "xxx",
    "XXX",
    "***",
)


def is_safe(value: str) -> bool:
    if any(marker in value for marker in REDACTED_MARKERS):
        return True
    # Credentials carry digits or mixed case; lowercase words ("database-password") are
    # placeholders. gitleaks, the other hook, covers vendor-specific formats.
    return not (any(c.isdigit() for c in value) or (value.lower() != value != value.upper()))


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
        for pattern in PATTERNS:
            if any(not is_safe(m.group("val")) for m in pattern.finditer(line)):
                hits.append((ln, line.strip()[:160]))
                break
    return hits


def main() -> int:
    blocked = False
    for p in sys.argv[1:]:
        for ln, line in scan(Path(p)):
            if not blocked:
                print("ERROR: pre-commit blocked plaintext-secret patterns:", file=sys.stderr)
            blocked = True
            print(f"  {p}:{ln}: {line}", file=sys.stderr)
    if blocked:
        print(file=sys.stderr)
        print("If this is a false positive (test fixture, documentation example),", file=sys.stderr)
        print("replace the value with REDACTED, <your_key_here>, or similar.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
