import math
from datetime import date, timedelta

import pytest

from factorlab.components.api.portfolio_analytics import (
    benchmark_values,
    daily_changes,
    drawdown,
    quantile,
    rebased,
    regression,
    risk_stats,
)
from factorlab.components.api.portfolio_performance import RateBook, add_analytics, build

D0 = date(2026, 1, 1)


def series(returns):
    """(date, cumulative, daily) rows for a list of daily returns."""
    rows, growth = [], 1.0
    for index, ret in enumerate(returns):
        growth *= 1 + ret
        rows.append((D0 + timedelta(days=index + 1), growth - 1, ret))
    return rows


def test_quantile_interpolates_like_numpy():
    assert quantile([1, 2, 3, 4, 5], 0.05) == pytest.approx(1.2)
    assert quantile([3.0], 0.05) == 3.0


def test_var_and_expected_shortfall_from_the_loss_tail():
    returns = [-0.05, -0.04] + [0.01] * 38  # 40 days
    stats = risk_stats(series(returns), 10_000, None, None)
    cut = quantile(returns, 0.05)
    assert stats.observations == 40
    assert stats.var95 == pytest.approx(-cut)
    assert stats.es95 == pytest.approx(
        -sum(r for r in returns if r <= cut) / len([r for r in returns if r <= cut])
    )
    assert stats.var95 is not None and stats.var95_amount == pytest.approx(stats.var95 * 10_000)
    mean = sum(returns) / 40
    vol = math.sqrt(sum((r - mean) ** 2 for r in returns) / 39)
    assert stats.daily_vol == pytest.approx(vol) and stats.annual_vol == pytest.approx(
        vol * math.sqrt(252)
    )
    assert stats.enough_history is False


def test_too_little_history_leaves_tail_risk_unknown():
    stats = risk_stats(series([0.01, -0.02, 0.01]), 1000, None, None)
    assert stats.var95 is None and stats.es95 is None and stats.beta is None
    assert stats.daily_vol is not None


def test_drawdown_is_measured_on_the_return_index():
    rows = series([0.10, -0.20, 0.05, 0.30])
    worst, peak, trough, current = drawdown([(day, cumulative) for day, cumulative, _ in rows])
    assert worst == pytest.approx(-0.20)
    assert peak == rows[0][0] and trough == rows[1][0]
    assert current == pytest.approx(0)  # 1.1 * 0.8 * 1.05 * 1.3 > 1.1: a new high


def test_regression_recovers_a_known_beta():
    xs = [0.01 * math.sin(i) for i in range(60)]
    beta, correlation = regression(xs, [2 * x for x in xs])
    assert beta == pytest.approx(2) and correlation == pytest.approx(1)
    assert regression(xs[:5], xs[:5]) == (None, None)


def test_benchmarks_convert_to_base_and_rebase_to_the_start():
    days = [date(2026, 1, 2), date(2026, 1, 5)]
    closes = [(date(2025, 12, 31), 100.0), (date(2026, 1, 2), 110.0)]  # no close on the 5th
    rates = {date(2025, 12, 31): 1.3, date(2026, 1, 2): 1.3, date(2026, 1, 5): 1.2}
    values = benchmark_values(days, closes, rates.get)
    assert values == [pytest.approx(143), pytest.approx(132)]  # the 2nd's close at the 5th's rate
    start = benchmark_values([date(2025, 12, 31)], closes, rates.get)[0]
    assert [point.value for point in rebased(days, values, start)] == [
        pytest.approx(0.1),
        pytest.approx(132 / 130 - 1),
    ]
    assert daily_changes(values, start)[1] == pytest.approx(132 / 143 - 1)


def test_report_gains_risk_and_a_risk_matched_benchmark():
    start = date(2025, 12, 31)
    bench, navs, closes = 100.0, [(start, 1000.0)], [(start, 100.0)]
    nav = 1000.0
    for index in range(40):
        day = start + timedelta(days=index + 1)
        move = 0.01 * math.sin(index + 1)
        bench *= 1 + move
        nav *= 1 + 1.5 * move  # the account moves 1.5x the benchmark
        navs.append((day, nav))
        closes.append((day, bench))
    report = build(
        currency="SGD",
        since=date(2026, 1, 1),
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
    )
    enriched = add_analytics(report, {"$SPX": closes}, lambda _day: 1.35, start)
    assert enriched.risk is not None and enriched.risk.beta == pytest.approx(1.5)
    assert enriched.risk.benchmark == "S&P 500 (index)"  # SPY has no history yet: $SPX stands in
    codes = [item.code for item in enriched.benchmarks]
    assert codes == ["sp500", "risk_matched"]
    spx, matched = enriched.benchmarks
    assert spx.twr == pytest.approx(bench / 100 - 1)
    assert enriched.twr is not None and spx.twr is not None
    assert spx.excess == pytest.approx(enriched.twr - spx.twr)
    assert matched.twr == pytest.approx(enriched.twr)  # exactly 1.5x every day


def test_report_without_benchmark_prices_still_has_risk():
    navs = [(date(2025, 12, 31), 1000.0), (date(2026, 1, 2), 1010.0)]
    report = build(
        currency="SGD",
        since=date(2026, 1, 1),
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
    )
    enriched = add_analytics(report, {}, lambda _day: 1.35, date(2025, 12, 31))
    assert enriched.benchmarks == [] and enriched.risk is not None
    assert any("No benchmark" in warning for warning in enriched.warnings)


def _report(days=30):
    start = date(2025, 12, 31)
    navs, closes, nav = (
        [(start, 1000.0)],
        {"SPY": [(start, 100.0)], "$SPX": [(start, 5000.0)], "BOXX": [(start, 110.0)]},
        1000.0,
    )
    for index in range(days):
        day = start + timedelta(days=index + 1)
        move = 0.01 * math.sin(index + 1)
        nav *= 1 + move
        navs.append((day, nav))
        closes["SPY"].append((day, closes["SPY"][-1][1] * (1 + move)))
        closes["$SPX"].append((day, closes["$SPX"][-1][1] * (1 + move / 2)))
        closes["BOXX"].append((day, closes["BOXX"][-1][1] * 1.0001))
    report = build(
        currency="SGD",
        since=date(2026, 1, 1),
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
    )
    return report, closes, start


def test_spy_is_preferred_and_cash_never_sets_beta():
    report, closes, start = _report()
    enriched = add_analytics(report, closes, lambda _day: 1.35, start)
    labels = {item.code: item.label for item in enriched.benchmarks}
    assert labels["sp500"] == "S&P 500 (SPY)"
    assert labels["cash"] == "Cash (BOXX)"
    assert enriched.risk is not None and enriched.risk.benchmark == "S&P 500 (SPY)"
    assert enriched.risk.beta == pytest.approx(1.0)
    cash = next(item for item in enriched.benchmarks if item.code == "cash")
    assert cash.twr == pytest.approx(1.0001**30 - 1)


def test_index_stands_in_until_the_etf_covers_the_period():
    report, closes, start = _report()
    closes["SPY"] = closes["SPY"][10:]  # history starts after the period's start
    enriched = add_analytics(report, closes, lambda _day: 1.35, start)
    sp500 = next(item for item in enriched.benchmarks if item.code == "sp500")
    assert sp500.label == "S&P 500 (index)" and sp500.note == "Price index; excludes dividends"


def test_cash_only_gives_no_beta():
    report, closes, start = _report()
    enriched = add_analytics(report, {"BOXX": closes["BOXX"]}, lambda _day: 1.35, start)
    assert [item.code for item in enriched.benchmarks] == ["cash"]
    assert enriched.risk is not None and enriched.risk.beta is None
