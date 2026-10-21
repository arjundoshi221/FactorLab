"""Index construction against hand-computed three- and four-stock cases (ADR 0017)."""

from datetime import date
from uuid import UUID

import pytest

from factorlab.indexes import (
    IndexDefinition,
    IndexEngine,
    Observations,
    Span,
    Timeline,
    load_catalog,
    rebalance_due,
    third_friday,
)

A, B, C, D = (UUID(int=i) for i in range(1, 5))
EW = IndexDefinition("tech_ew", "Tech EW", "ew", "45")
CW = IndexDefinition("tech_cw", "Tech CW", "cw", "45")
ALL = IndexDefinition("all_cw", "All CW", "cw", None)


def engine(defs, members=None, shares=None, base=date(2026, 3, 2)):
    timeline = members or Timeline(
        [
            Span(A, "45", date(2026, 1, 1)),
            Span(B, "45", date(2026, 1, 1)),
            Span(C, "40", date(2026, 1, 1)),
        ]
    )
    counts = shares or Observations(
        {
            A: [(date(2026, 1, 1), 100.0)],
            B: [(date(2026, 1, 1), 300.0)],
            C: [(date(2026, 1, 1), 50.0)],
        }
    )
    return IndexEngine(defs, base_date=base, members=timeline.on, shares=counts.on)


def by_code(days):
    return {d.code: d for d in days}


def test_third_friday_and_quarterly_rebalance_schedule():
    assert third_friday(2026, 3) == date(2026, 3, 20)
    assert third_friday(2026, 5) == date(2026, 5, 15)
    assert not rebalance_due(date(2026, 3, 2), date(2026, 3, 19))
    assert rebalance_due(date(2026, 3, 2), date(2026, 3, 20))
    assert rebalance_due(date(2026, 3, 19), date(2026, 3, 23))  # Friday was a holiday
    assert not rebalance_due(date(2026, 3, 20), date(2026, 6, 18))
    assert rebalance_due(date(2025, 12, 30), date(2026, 1, 5)) is False


def test_equal_weight_drifts_between_rebalances():
    e = engine([EW])
    [start] = e.step(date(2026, 3, 2), {A: 10.0, B: 20.0, C: 5.0})
    assert (start.level, start.ret, start.n_constituents) == (1000.0, None, 2)
    [day] = e.step(date(2026, 3, 3), {A: 11.0, B: 20.0, C: 5.0})  # A +10%, B flat
    assert day.ret == pytest.approx(0.05) and day.level == pytest.approx(1050.0)
    weights = {c.listing: c.weight for c in day.constituents}
    assert weights == {A: pytest.approx(0.5), B: pytest.approx(0.5)}
    assert sum(c.contribution for c in day.constituents) == pytest.approx(day.ret)
    [nxt] = e.step(date(2026, 3, 4), {A: 11.0, B: 22.0, C: 5.0})  # B +10% on drifted weights
    drifted = {c.listing: c.weight for c in nxt.constituents}
    assert drifted[A] == pytest.approx(0.55 / 1.05) and drifted[B] == pytest.approx(0.5 / 1.05)
    assert nxt.ret == pytest.approx(0.1 * 0.5 / 1.05)


def test_equal_weight_resets_on_quarterly_rebalance_and_admits_new_members():
    members = Timeline(
        [
            Span(A, "45", date(2026, 1, 1)),
            Span(B, "45", date(2026, 1, 1)),
            Span(D, "45", date(2026, 3, 10)),
        ]  # D joins mid-quarter
    )
    e = engine([EW], members=members)
    e.step(date(2026, 3, 2), {A: 10.0, B: 10.0})
    e.step(date(2026, 3, 11), {A: 20.0, B: 10.0, D: 10.0})
    [before] = e.step(date(2026, 3, 19), {A: 20.0, B: 10.0, D: 10.0})
    assert {c.listing for c in before.constituents} == {A, B}  # D waits
    e.step(date(2026, 3, 20), {A: 20.0, B: 10.0, D: 10.0})  # rebalance at this close
    [after] = e.step(date(2026, 3, 23), {A: 20.0, B: 10.0, D: 10.0})
    assert {c.listing: c.weight for c in after.constituents} == {
        A: pytest.approx(1 / 3),
        B: pytest.approx(1 / 3),
        D: pytest.approx(1 / 3),
    }


def test_deleted_member_drops_and_rest_renormalise():
    members = Timeline(
        [Span(A, "45", date(2026, 1, 1)), Span(B, "45", date(2026, 1, 1), date(2026, 3, 3))]
    )
    e = engine([EW], members=members)
    e.step(date(2026, 3, 2), {A: 10.0, B: 10.0})
    e.step(date(2026, 3, 3), {A: 10.0, B: 10.0})
    [kept] = e.step(date(2026, 3, 4), {A: 10.0, B: 10.0})  # still a member at the 3 Mar close
    assert len(kept.constituents) == 2
    [day] = e.step(date(2026, 3, 5), {A: 10.0, B: 10.0})  # dropped at the 4 Mar close
    assert {c.listing: c.weight for c in day.constituents} == {A: pytest.approx(1.0)}


def test_cap_weight_uses_close_times_shares_each_day():
    e = engine([CW, ALL])
    e.step(date(2026, 3, 2), {A: 10.0, B: 10.0, C: 20.0})
    days = by_code(e.step(date(2026, 3, 3), {A: 12.0, B: 10.0, C: 20.0}))
    tech = days["tech_cw"]
    # Caps at the 2 Mar close: A 1000, B 3000 -> 25% / 75%.
    assert {c.listing: c.weight for c in tech.constituents} == {
        A: pytest.approx(0.25),
        B: pytest.approx(0.75),
    }
    assert tech.ret == pytest.approx(0.25 * 0.2)
    assert {c.listing for c in days["all_cw"].constituents} == {A, B, C}
    [tech2, _] = e.step(date(2026, 3, 4), {A: 12.0, B: 10.0, C: 20.0})
    # Caps at the 3 Mar close: A 1200, B 3000.
    assert tech2.constituents[0].weight == pytest.approx(1200 / 4200)


def test_coverage_gate_flags_but_still_computes():
    e = engine([EW])
    e.step(date(2026, 3, 2), {A: 10.0, B: 10.0})
    [day] = e.step(date(2026, 3, 3), {A: 11.0})  # B has no close today
    assert day.coverage == pytest.approx(0.5) and not day.complete
    assert day.ret == pytest.approx(0.05)
    missing = {c.listing: c.ret for c in day.constituents}[B]
    assert missing is None


def test_days_before_base_only_warm_prices_and_order_is_enforced():
    e = engine([EW], base=date(2026, 3, 3))
    assert e.step(date(2026, 3, 2), {A: 10.0, B: 10.0}) == []
    [start] = e.step(date(2026, 3, 3), {A: 11.0, B: 10.0})
    assert start.level == 1000.0
    with pytest.raises(ValueError):
        e.step(date(2026, 3, 3), {})


def test_timelines_clamp_to_the_earliest_snapshot_for_backfill():
    timeline = Timeline([Span(A, "45", date(2026, 10, 1))])
    assert timeline.on(date(2016, 1, 4)) == {A: "45"}
    assert (
        Timeline([Span(A, "45", date(2026, 10, 1))], clamp_before_first=False).on(date(2016, 1, 4))
        == {}
    )
    shares = Observations({A: [(date(2026, 10, 1), 5.0), (date(2026, 10, 20), 6.0)]})
    assert shares.on(A, date(2016, 1, 4)) == 5.0 and shares.on(A, date(2026, 10, 21)) == 6.0


def test_repository_catalogue_defines_composites_and_two_per_sector():
    catalog = load_catalog()
    codes = [d.code for d in catalog.definitions()]
    assert codes[:2] == ["r3k_iwv_ew", "r3k_iwv_cw"] and len(codes) == 2 + 2 * 11
    assert "us_gics_45_cw" in codes
    assert {"$SPX", "XLK", "RSPT", "IWV"} <= set(catalog.benchmark_symbols())


def test_universe_seed_collects_every_benchmark_symbol():
    import yaml

    from factorlab.core.paths import home

    seed = yaml.safe_load((home() / "deploy" / "us-universe.yaml").read_text(encoding="utf-8"))
    missing = set(load_catalog().benchmark_symbols()) - set(seed["extra_symbols"])
    assert not missing, f"add to deploy/us-universe.yaml extra_symbols: {sorted(missing)}"
