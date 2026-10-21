"""US index and sector performance reads (ADR 0017)."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.indexes import IndexRepository, period_return, repository


def result(columns, values):
    return SimpleNamespace(column_names=columns, result_rows=values)


def test_period_return_uses_the_last_point_on_or_before_each_anchor():
    points = [
        (date(2025, 12, 31), 100.0),
        (date(2026, 9, 30), 110.0),
        (date(2026, 10, 20), 121.0),
        (date(2026, 10, 21), 133.1),
    ]
    as_of = date(2026, 10, 21)
    assert period_return(points, as_of, None) == pytest.approx(0.1)
    assert period_return(points, as_of, "ytd") == pytest.approx(0.331)
    assert period_return(points, as_of, timedelta(days=7)) == pytest.approx(133.1 / 110 - 1)
    assert period_return(points, as_of, timedelta(days=3650)) is None
    assert period_return(points[:1], as_of, None) is None


def test_series_rejects_unknown_codes_and_reads_both_kinds():
    client = Mock()
    client.query.side_effect = [
        result(
            ["index_code", "trade_date", "level", "complete"],
            [
                ("us_gics_45_cw", date(2026, 10, 20), 1000.0, True),
                ("us_gics_45_cw", date(2026, 10, 21), 1010.0, False),
            ],
        ),
        result(["symbol", "trade_date", "close"], [("XLK", date(2026, 10, 21), 250.5)]),
    ]
    app.dependency_overrides[repository] = lambda: IndexRepository(client)
    try:
        http = TestClient(app)
        assert (
            http.get("/hub/api/v1/us/indexes/series", params={"codes": ["NOPE"]}).status_code == 404
        )
        response = http.get(
            "/hub/api/v1/us/indexes/series",
            params={
                "codes": ["us_gics_45_cw", "XLK"],
                "date_from": "2026-10-01",
                "date_to": "2026-10-21",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    items = {item["code"]: item for item in response.json()["items"]}
    assert items["us_gics_45_cw"]["kind"] == "constructed"
    assert [p["complete"] for p in items["us_gics_45_cw"]["points"]] == [True, False]
    assert items["XLK"]["kind"] == "tracked" and items["XLK"]["points"][0]["value"] == 250.5
    tracked_sql = client.query.call_args_list[1].args[0]
    assert (
        "alias_kind = 'schwab_symbol'" in tracked_sql
        and "FROM market.bars AS b FINAL" in tracked_sql
    )
    assert client.query.call_args_list[1].kwargs["parameters"]["source"] == "schwab"


def test_sector_performance_shapes_every_sector_with_benchmarks():
    repo = IndexRepository(Mock())
    days = [date(2025, 12, 31), date(2026, 10, 20), date(2026, 10, 21)]

    def constructed(codes, start, end):
        from factorlab.components.api.indexes import SeriesPoint

        return {
            c: [SeriesPoint(trade_date=d, value=100.0 + i) for i, d in enumerate(days)]
            for c in codes
        }

    def tracked(symbols, start, end):
        from factorlab.components.api.indexes import SeriesPoint

        return {s: [SeriesPoint(trade_date=d, value=50.0) for d in days] for s in symbols}

    repo.constructed = constructed
    repo.tracked = tracked

    def movers(code: str, trade_date: date, n: int = 5) -> dict[str, list[dict[str, object]]]:
        return {"top": [{"symbol": "NVDA"}], "bottom": []}

    repo.movers = movers
    from factorlab.components.api.indexes import sector_performance

    body = sector_performance(repo, date(2026, 10, 21), "ew")
    assert body["as_of"] == date(2026, 10, 21) and len(body["sectors"]) == 11
    tech = next(s for s in body["sectors"] if s["sector_id"] == "45")
    assert tech["code"] == "us_gics_45_ew" and tech["cw_etf"] == "XLK" and tech["ew_etf"] == "RSPT"
    assert tech["returns"]["ew"]["1d"] == pytest.approx(102 / 101 - 1)
    assert tech["returns"]["cw_etf"]["ytd"] == 0.0
    assert tech["movers"]["top"][0]["symbol"] == "NVDA"
    assert set(body["benchmarks"]) == {"composite_ew", "composite_cw", "$SPX", "RSP"}


def test_constituents_404_for_tracked_codes_and_empty_before_first_run():
    client = Mock()
    client.query.return_value = result(["trade_date"], [(date(1970, 1, 1),)])
    repo = IndexRepository(client)
    with pytest.raises(Exception, match="Unknown constructed index"):
        repo.constituents("XLK", None, 10)
    assert repo.constituents("us_gics_45_cw", None, 10)["items"] == []
