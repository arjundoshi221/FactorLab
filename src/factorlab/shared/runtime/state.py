"""Per-source RunState — last_ok / last_fail / retry_after_at cooldown gate.

Lifted from the political runner so every orchestrator can re-use the same
"is this source eligible to run NOW?" decision. Backed by a small JSON file
on disk; tolerant of partial / malformed entries.

Decision rules (in ``should_run``):
  1. ``retry_after_at`` is set AND in the future → SKIP (deferred cooldown).
  2. ``last_ok`` is within ``success_cooldown_hours`` → SKIP (recently ok).
  3. Otherwise → RUN.

``--mode daily`` / ``--mode weekly`` typically pass ``success_cooldown_hours=0``
(always run). ``--mode hourly`` uses a default of 6h so it only retries sources
that haven't checked in recently.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class RunState:
    """Last-run record for ONE source. All times UTC ISO strings."""

    last_ok: str | None = None
    last_fail: str | None = None
    retry_after_at: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def load_run_state(path: Path) -> dict[str, RunState]:
    """Load *path* → ``{source: RunState}``. Returns ``{}`` if missing/malformed."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("[run-state] could not parse %s: %s -- starting fresh", path, e)
        return {}
    out: dict[str, RunState] = {}
    for source, d in (raw or {}).items():
        if not isinstance(d, dict):
            continue
        out[source] = RunState(**{
            k: v for k, v in d.items()
            if k in RunState.__dataclass_fields__
        })
    return out


def save_run_state(path: Path, state: dict[str, RunState]) -> None:
    """Write *path* atomically. Creates parent if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {k: v.to_dict() for k, v in state.items()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(serializable, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def should_run(
    source: str,
    state: dict[str, RunState],
    *,
    success_cooldown_hours: float = 0.0,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Decide whether *source* is eligible to run NOW. Returns ``(eligible, reason)``."""
    now = now or datetime.now(timezone.utc)
    rs = state.get(source)
    if rs is None:
        return True, "first run"

    if rs.retry_after_at:
        try:
            ra = datetime.fromisoformat(rs.retry_after_at)
            if now < ra:
                wait = (ra - now).total_seconds()
                return False, f"deferred ({wait:.0f}s remaining)"
        except Exception:
            pass

    if rs.last_ok and success_cooldown_hours > 0:
        try:
            ts = datetime.fromisoformat(rs.last_ok)
            age = (now - ts).total_seconds()
            if age < success_cooldown_hours * 3600:
                return False, f"recently succeeded ({age / 3600:.1f}h ago)"
        except Exception:
            pass

    return True, "eligible"


def mark_ok(source: str, state: dict[str, RunState]) -> None:
    """Record a successful run. Clears any deferred cooldown."""
    rs = state.setdefault(source, RunState())
    rs.last_ok = datetime.now(timezone.utc).isoformat()
    rs.retry_after_at = None
    rs.consecutive_failures = 0
    rs.last_error = None


def mark_fail(
    source: str,
    state: dict[str, RunState],
    *,
    retry_after_sec: float | None = None,
    error: str | None = None,
) -> None:
    """Record a failure. If *retry_after_sec* is set, the source goes into
    deferred cooldown until ``now + retry_after_sec``.
    """
    now = datetime.now(timezone.utc)
    rs = state.setdefault(source, RunState())
    rs.last_fail = now.isoformat()
    rs.consecutive_failures += 1
    rs.last_error = (error or "")[:500]
    if retry_after_sec is not None and retry_after_sec > 0:
        rs.retry_after_at = (now + timedelta(seconds=retry_after_sec)).isoformat()


__all__ = [
    "RunState",
    "load_run_state",
    "save_run_state",
    "should_run",
    "mark_ok",
    "mark_fail",
]
