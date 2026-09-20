"""IBKR pacing (rate-limit) helpers.

IBKR historical-data pacing (per docs/data-sources/06-ibkr.md §6):

* Global cap: 60 requests / 600s rolling window. We cap at 50 for headroom.
* Identical-request cooldown: 15 seconds.
* Exponential backoff on error 162.

Client-side only — the Gateway will still error 162 if we're wrong; the
limiter's job is to keep us out of that state most of the time.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable

from factorlab.sources.ibkr.errors import IBKRPacingError

log = logging.getLogger(__name__)

# Defaults tuned to docs/data-sources/06-ibkr.md §6
DEFAULT_WINDOW_SEC = 600.0
DEFAULT_MAX_REQUESTS = 50
DEFAULT_IDENTICAL_COOLDOWN_SEC = 15.0
DEFAULT_BACKOFF_BASE_SEC = 30.0
DEFAULT_BACKOFF_MAX_SEC = 300.0


class RateLimiter:
    """Rolling-window limiter with per-request-key cooldown.

    Not thread-safe; ``us_portfolio_ibkr_snapshot`` runs single-threaded per
    Gateway. Time source injectable for tests.
    """

    def __init__(
        self,
        *,
        window_sec: float = DEFAULT_WINDOW_SEC,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        identical_cooldown_sec: float = DEFAULT_IDENTICAL_COOLDOWN_SEC,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.window_sec = window_sec
        self.max_requests = max_requests
        self.identical_cooldown_sec = identical_cooldown_sec
        self._clock = clock
        self._sleep = sleep
        self._events: deque[float] = deque()
        self._last_seen: dict[str, float] = {}

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def wait_for_slot(self, key: str, *, weight: int = 1) -> None:
        """Block until a slot is available and mark the request.

        ``key`` identifies an "identical request" for the 15s cooldown rule
        (e.g. ``f"{conid}:{bar_size}:{what_to_show}"``).
        ``weight`` counts against the rolling cap; use ``2`` for BID_ASK.
        """
        now = self._clock()
        self._evict(now)

        # 1. identical-request cooldown
        last = self._last_seen.get(key)
        if last is not None:
            wait = self.identical_cooldown_sec - (now - last)
            if wait > 0:
                log.debug("Cooldown %.2fs on key=%s", wait, key)
                self._sleep(wait)
                now = self._clock()
                self._evict(now)

        # 2. rolling-window cap
        while len(self._events) + weight > self.max_requests:
            oldest = self._events[0]
            wait = (oldest + self.window_sec) - now
            if wait <= 0:
                self._evict(now)
                continue
            log.info(
                "Pacing cap reached (%d in %.0fs); sleeping %.1fs",
                len(self._events), self.window_sec, wait,
            )
            self._sleep(wait)
            now = self._clock()
            self._evict(now)

        for _ in range(weight):
            self._events.append(now)
        self._last_seen[key] = now


def backoff_for(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE_SEC,
    cap: float = DEFAULT_BACKOFF_MAX_SEC,
) -> float:
    """Exponential backoff duration for error-162 (pacing) retries.

    ``attempt`` is 1-based. Doubles up to ``cap``.
    """
    if attempt < 1:
        raise IBKRPacingError(f"attempt must be >=1, got {attempt}")
    return min(cap, base * (2 ** (attempt - 1)))
