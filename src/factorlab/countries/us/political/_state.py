"""Back-compat shim — political-side `_state` now re-exports from
:mod:`factorlab.shared.ingest.state`.

Existing political ingest modules construct ``State(source='house_clerk_ptr')``
and the file lands under ``state_dir('political') / 'house_clerk_ptr.json'``
(the historical nested layout). We achieve that with the ``namespace='political'``
argument of the shared State class.

New code should import directly from :mod:`factorlab.shared.ingest.state`.
"""

from __future__ import annotations

from factorlab.shared.ingest.state import State as _SharedState
from factorlab.shared.paths import REPO_ROOT, state_dir  # noqa: F401

STATE_DIR = state_dir("political")


class State(_SharedState):
    """``State(source)`` → ``state_dir('political')/<source>.json`` (legacy nested)."""

    def __init__(self, source: str) -> None:
        super().__init__(source, namespace="political")


__all__ = ["State", "STATE_DIR", "REPO_ROOT"]
