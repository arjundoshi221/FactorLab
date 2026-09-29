"""Which build this process runs, as stamped into its image.

Per-component images set ``FACTORLAB_COMPONENT``, ``FACTORLAB_VERSION`` and
``FACTORLAB_COMMIT`` at build time. The legacy monolith image sets
``FACTORLAB_RELEASE_ID`` (``<UTC>-<sha>``) and ``FACTORLAB_COMMIT`` instead; its
compose services set ``FACTORLAB_COMPONENT``. Anything unset is ``None``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BuildInfo:
    component: str | None
    version: str | None
    commit: str | None
    release_id: str | None


def build_info() -> BuildInfo:
    def value(name: str) -> str | None:
        return os.getenv(name) or None

    return BuildInfo(
        component=value("FACTORLAB_COMPONENT"),
        version=value("FACTORLAB_VERSION"),
        commit=value("FACTORLAB_COMMIT"),
        release_id=value("FACTORLAB_RELEASE_ID"),
    )
