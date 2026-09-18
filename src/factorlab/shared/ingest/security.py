"""URL / error / path safety helpers — lifted from political/_security.

Reused by every source that fetches data over HTTP:

- :func:`redact_url` strips API-key query params before logging or persisting
  (Finnhub ``?token=``, FEC ``?api_key=``, Congress.gov ``?api_key=``, ...).
- :func:`redact_error` does the same for exception messages (which often embed
  the failing URL).
- :func:`validate_id_segment` rejects strings that could escape a cache root
  via path traversal (poisoned upstream ``doc_id``, ``filing_id``, ...).
- :func:`assert_under_root` post-resolution check that a ``Path`` stays
  within the expected root.
"""

from __future__ import annotations

import re
from pathlib import Path

SECRET_QS_KEYS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "token",
    "access_token",
    "key",
    "auth",
)

_REDACT_QS_RE = re.compile(
    rf"({'|'.join(re.escape(k) for k in SECRET_QS_KEYS)})=[^&\s]*",
    flags=re.IGNORECASE,
)

_ID_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_YEAR_RE = re.compile(r"^\d{4}$")


def redact_url(url: str | None) -> str | None:
    """Replace ``api_key=...`` / ``token=...`` etc. with ``...=REDACTED``.

    Idempotent — already-redacted URLs are unchanged.
    """
    if not url:
        return url
    return _REDACT_QS_RE.sub(lambda m: f"{m.group(1)}=REDACTED", url)


def redact_error(s: str | None) -> str | None:
    """Scrub a string-form exception (which often contains the failing URL)."""
    if not s:
        return s
    return redact_url(s)


def validate_id_segment(s: str, *, name: str = "id") -> str:
    """Validate a path-segment-shaped identifier (year, doc_id, filing_id).

    Raises ``ValueError`` if ``s`` contains anything outside ``[A-Za-z0-9_-]``
    or is empty / too long. Used at every call site that builds a cache
    filename from upstream-supplied data.
    """
    if not s or not _ID_SEGMENT_RE.match(s):
        raise ValueError(f"unsafe {name} segment: {s!r}")
    return s


def validate_year_segment(s: str | int) -> str:
    """Validate a 4-digit year string used in cache paths."""
    s = str(s)
    if not _YEAR_RE.match(s):
        raise ValueError(f"unsafe year segment: {s!r}")
    return s


def assert_under_root(p: Path, root: Path) -> Path:
    """Resolve ``p`` and assert it stays under ``root``.

    Raises ``ValueError`` if not. This is the ingest-side variant; the
    paths-side variant (:func:`factorlab.shared.paths.assert_under_root`)
    raises ``PermissionError`` instead. Both check the same invariant.
    """
    rp = p.resolve()
    rr = root.resolve()
    try:
        rp.relative_to(rr)
    except ValueError as e:
        raise ValueError(
            f"path traversal attempt: {p} resolves to {rp}, outside {rr}"
        ) from e
    return rp


__all__ = [
    "SECRET_QS_KEYS",
    "redact_url",
    "redact_error",
    "validate_id_segment",
    "validate_year_segment",
    "assert_under_root",
]
