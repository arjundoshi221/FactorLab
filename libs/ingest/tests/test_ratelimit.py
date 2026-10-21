"""The limiter reports and reserves free slots without blocking."""

from factorlab.ingest.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_delay_and_try_acquire_follow_the_tightest_window():
    clock = Clock()
    limiter = SlidingWindowLimiter([(1.0, 1), (60.0, 2)], clock=clock, sleep=clock.sleep)
    assert limiter.delay() == 0
    assert limiter.try_acquire()
    assert limiter.delay() == 1.0
    assert not limiter.try_acquire()
    clock.now = 1.0
    assert limiter.try_acquire()
    assert limiter.delay() == 59.0
    limiter.acquire()
    assert clock.now == 60.0
