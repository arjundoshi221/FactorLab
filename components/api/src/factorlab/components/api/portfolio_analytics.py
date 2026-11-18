"""Statistical risk and benchmark comparison over a performance report's daily returns.

Pure functions: they take the report's daily time-weighted returns (deposits already
removed, so a deposit never reads as a gain) and benchmark closes, and never query.

* Volatility is the sample standard deviation of daily returns; annualised by sqrt(252).
* Historical VaR / expected shortfall at 95%: the loss at the 5th percentile of daily
  returns (linear interpolation) and the mean loss at or beyond it, as fractions of NAV
  and in base currency at the closing NAV.
* Maximum drawdown is on the cumulative TWR index, so deposits cannot hide a loss.
* Benchmarks are valued on the report's NAV dates (last close on or before each date),
  converted to the base currency at that date's rate, and rebased to the start.
* Beta and correlation regress daily portfolio returns on the first benchmark's daily
  returns over the same dates. The risk-matched benchmark compounds beta x the
  benchmark's daily return: what the benchmark would have done at the account's risk.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable, Sequence
from datetime import date

from pydantic import BaseModel

TRADING_DAYS = 252
MIN_OBSERVATIONS = 20


class RiskStats(BaseModel):
    observations: int
    daily_vol: float | None
    annual_vol: float | None
    var95: float | None  # loss fraction (positive = loss)
    es95: float | None
    var95_amount: float | None  # in base currency at the closing NAV
    es95_amount: float | None
    max_drawdown: float | None  # negative fraction
    drawdown_peak: date | None
    drawdown_trough: date | None
    current_drawdown: float | None
    beta: float | None
    correlation: float | None
    benchmark: str | None
    enough_history: bool  # at least 252 daily returns


class BenchmarkPoint(BaseModel):
    date: date
    value: float  # cumulative return since the start


class Benchmark(BaseModel):
    code: str
    label: str
    note: str
    twr: float | None
    excess: float | None  # portfolio TWR minus this return
    points: list[BenchmarkPoint]


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy's default)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def stdev(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def drawdown(points: Sequence[tuple[date, float]]) -> tuple[float, date | None, date | None, float]:
    """(max drawdown, peak date, trough date, current drawdown) of a cumulative-return series
    that starts from 0 (growth index 1)."""
    peak_value, peak_day = 1.0, None
    worst, worst_peak, worst_trough = 0.0, None, None
    growth = 1.0
    for day, cumulative in points:
        growth = 1 + cumulative
        if growth > peak_value:
            peak_value, peak_day = growth, day
        fall = growth / peak_value - 1
        if fall < worst:
            worst, worst_peak, worst_trough = fall, peak_day, day
    return worst, worst_peak, worst_trough, growth / peak_value - 1


def regression(xs: Sequence[float], ys: Sequence[float]) -> tuple[float | None, float | None]:
    """(beta of ys on xs, correlation); None when there is too little data or no variance."""
    if len(xs) < MIN_OBSERVATIONS:
        return None, None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0:
        return None, None
    return cov / vx, (cov / math.sqrt(vx * vy) if vy > 0 else None)


def value_on(closes: Sequence[tuple[date, float]], day: date) -> float | None:
    """The last close on or before ``day``."""
    index = bisect_right([d for d, _ in closes], day)
    return closes[index - 1][1] if index else None


def benchmark_values(
    days: Sequence[date],
    closes: Sequence[tuple[date, float]],
    rate: Callable[[date], float | None],
) -> list[float | None]:
    """Benchmark value in base currency on each day (None without a close or a rate)."""
    closes = sorted(closes)
    result: list[float | None] = []
    for day in days:
        close, fx = value_on(closes, day), rate(day)
        result.append(close * fx if close is not None and fx else None)
    return result


def risk_stats(
    days: Sequence[tuple[date, float, float | None]],
    closing_nav: float | None,
    bench_returns: Sequence[float | None] | None,
    bench_label: str | None,
) -> RiskStats:
    """``days`` = (date, cumulative TWR, daily return); ``bench_returns`` align with ``days``."""
    returns = [ret for _, _, ret in days if ret is not None]
    count = len(returns)
    vol = stdev(returns) if count >= 2 else None
    var = es = None
    if count >= MIN_OBSERVATIONS:
        cut = quantile(returns, 0.05)
        tail = [ret for ret in returns if ret <= cut]
        var, es = -cut, -sum(tail) / len(tail)
    worst, peak, trough, current = drawdown([(day, cumulative) for day, cumulative, _ in days])
    beta = correlation = None
    if bench_returns is not None:
        pairs = [
            (bench, ret)
            for (_, _, ret), bench in zip(days, bench_returns, strict=True)
            if ret is not None and bench is not None
        ]
        beta, correlation = regression([b for b, _ in pairs], [r for _, r in pairs])
    amount = lambda share: share * closing_nav if share is not None and closing_nav else None  # noqa: E731
    return RiskStats(
        observations=count,
        daily_vol=vol,
        annual_vol=vol * math.sqrt(TRADING_DAYS) if vol is not None else None,
        var95=var,
        es95=es,
        var95_amount=amount(var),
        es95_amount=amount(es),
        max_drawdown=worst if days else None,
        drawdown_peak=peak,
        drawdown_trough=trough,
        current_drawdown=current if days else None,
        beta=beta,
        correlation=correlation,
        benchmark=bench_label if beta is not None else None,
        enough_history=count >= TRADING_DAYS,
    )


def daily_changes(values: Sequence[float | None], start: float | None) -> list[float | None]:
    """Return between consecutive values (the first against ``start``)."""
    result: list[float | None] = []
    previous = start
    for value in values:
        result.append(value / previous - 1 if value is not None and previous else None)
        if value is not None:
            previous = value
    return result


def rebased(
    days: Sequence[date], values: Sequence[float | None], start: float | None
) -> list[BenchmarkPoint]:
    return [
        BenchmarkPoint(date=day, value=value / start - 1)
        for day, value in zip(days, values, strict=True)
        if value is not None and start
    ]


def compound_series(days: Sequence[date], returns: Sequence[float | None]) -> list[BenchmarkPoint]:
    growth, points = 1.0, []
    for day, ret in zip(days, returns, strict=True):
        growth *= 1 + (ret or 0)
        points.append(BenchmarkPoint(date=day, value=growth - 1))
    return points
