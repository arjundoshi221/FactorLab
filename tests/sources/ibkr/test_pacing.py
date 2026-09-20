"""Tests for pacing.RateLimiter and backoff."""

from __future__ import annotations

import pytest

from factorlab.sources.ibkr.errors import IBKRPacingError
from factorlab.sources.ibkr.pacing import RateLimiter, backoff_for


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, sec: float) -> None:
        self.slept.append(sec)
        self.t += max(0.0, sec)


def _limiter(max_requests=3, cooldown=15.0, window=600.0):
    clock = FakeClock()
    lim = RateLimiter(
        window_sec=window, max_requests=max_requests,
        identical_cooldown_sec=cooldown,
        clock=clock.now, sleep=clock.sleep,
    )
    return lim, clock


def test_cap_forces_wait():
    lim, clock = _limiter(max_requests=2, window=100.0)
    lim.wait_for_slot("a")   # t=0
    clock.t = 10.0
    lim.wait_for_slot("b")   # t=10
    clock.t = 20.0
    # 3rd request must wait until t=0 falls out of window => 100s from t=0
    lim.wait_for_slot("c")
    # slept enough to cross the boundary
    assert sum(clock.slept) >= 80.0


def test_identical_request_cooldown():
    lim, clock = _limiter(max_requests=10, cooldown=15.0)
    lim.wait_for_slot("aapl-1d-TRADES")
    clock.t = 5.0
    lim.wait_for_slot("aapl-1d-TRADES")
    # should have slept 10s to reach the 15s cooldown from t=0
    assert clock.slept and clock.slept[-1] == pytest.approx(10.0)


def test_bid_ask_weight_double_counts():
    lim, clock = _limiter(max_requests=3, window=100.0)
    lim.wait_for_slot("x", weight=2)  # occupies 2 slots
    lim.wait_for_slot("y", weight=1)  # occupies 1 slot
    clock.t = 1.0
    # a 4th request (weight=1) would exceed cap => must wait for oldest to fall off
    lim.wait_for_slot("z")
    assert sum(clock.slept) > 0


def test_backoff_grows_and_caps():
    assert backoff_for(1, base=30.0) == 30.0
    assert backoff_for(2, base=30.0) == 60.0
    assert backoff_for(3, base=30.0) == 120.0
    assert backoff_for(10, base=30.0, cap=300.0) == 300.0


def test_backoff_zero_attempt_rejected():
    with pytest.raises(IBKRPacingError):
        backoff_for(0)


def test_events_evicted_after_window():
    lim, clock = _limiter(max_requests=2, window=10.0)
    lim.wait_for_slot("a")
    lim.wait_for_slot("b")
    clock.t = 100.0  # long after window
    # Should not need to sleep — the two old events are outside the window
    prior_sleeps = list(clock.slept)
    lim.wait_for_slot("c")
    assert clock.slept == prior_sleeps  # no new sleeps
