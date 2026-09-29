"""Sliding-window client-side rate limiter configured from provider settings.

Generalises the Upstox limiter (``countries/in_/equities/upstox/candles.py``)
so every adapter can take its windows from ``configs/sources/<provider>.yaml``
instead of hard-coding them.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence


class SlidingWindowLimiter:
    """Block until one more request fits every ``(window_seconds, max_requests)`` bucket."""

    def __init__(
        self,
        windows: Sequence[tuple[float, int]],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        cleaned = tuple(sorted((float(w), int(n)) for w, n in windows if n > 0))
        if not cleaned:
            raise ValueError("at least one positive rate-limit window is required")
        self._windows = cleaned
        self._clock = clock
        self._sleep = sleep
        self._requests: deque[float] = deque()

    @property
    def windows(self) -> tuple[tuple[float, int], ...]:
        return self._windows

    def acquire(self) -> None:
        while True:
            now = self._clock()
            longest = self._windows[-1][0]
            while self._requests and now - self._requests[0] >= longest:
                self._requests.popleft()
            wait_for = 0.0
            stamps = list(self._requests)
            for window, limit in self._windows:
                recent = [stamp for stamp in stamps if now - stamp < window]
                if len(recent) >= limit:
                    wait_for = max(wait_for, window - (now - recent[-limit]))
            if wait_for <= 0:
                self._requests.append(now)
                return
            self._sleep(wait_for)


__all__ = ["SlidingWindowLimiter"]
