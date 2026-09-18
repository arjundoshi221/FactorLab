"""Back-compat shim — political-side `_security` now re-exports from
:mod:`factorlab.shared.ingest.security`.

The actual implementation lives in shared/ingest/. Existing callers
inside political/ keep using ``from factorlab.countries.us.political._security
import redact_url, ...`` and get the same behaviour. New code should
import directly from ``factorlab.shared.ingest.security``.
"""

from factorlab.shared.ingest.security import (  # noqa: F401
    SECRET_QS_KEYS,
    assert_under_root,
    redact_error,
    redact_url,
    validate_id_segment,
    validate_year_segment,
)

__all__ = [
    "SECRET_QS_KEYS",
    "redact_url",
    "redact_error",
    "validate_id_segment",
    "validate_year_segment",
    "assert_under_root",
]
