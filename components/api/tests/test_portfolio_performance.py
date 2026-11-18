from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.components.api.portfolio_performance import (
    Converted,
    RateBook,
    build,
    convert,
    daily_returns,
    ibkr_attribution,
    xirr,
)

USER = "d677331a-a04f-454a-8a1e-02425be2c101"
SINCE = date(2026, 1, 1)


def report(navs, flows, *, cash_lines=None, book=None, since=SINCE):
    return build(
        currency="SGD",
        since=since,
        nav_rows=navs,
        flow_rows=flows,
        cash_lines=cash_lines or {},
        book=book or RateBook(base="SGD"),
    )


def test_xirr_matches_excel_on_a_simple_year():
    assert xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)]) == pytest.approx(
        0.10, abs=1e-9
    )


def test_xirr_is_undefined_without_both_signs():
    assert xirr([(date(2026, 1, 1), 100), (date(2026, 2, 1), 100)]) is None
    assert xirr([(date(2026, 1, 1), -100)]) is None


def test_start_of_day_flows_and_chained_twr():
    navs = [(date(2026, 1, 2), 110.0), (date(2026, 1, 5), 330.0)]
    days = daily_returns((date(2025, 12, 31), 100.0), navs, {date(2026, 1, 3): 200.0})
    assert days[0].ret == pytest.approx(0.10)
    # 330 - 110 - 200 = 20 on a base of 110 + 200 (the deposit arrives before the day).
    assert days[1].flow == 200 and days[1].ret == pytest.approx(20 / 310)


def test_statement_figures_reconcile():
    """Opening NAV S$1,993.70, S$39,500 deposited in 2026, NAV S$44,443.05 on 12 Nov."""
    navs = [
        (date(2025, 12, 31), 1993.70),
        (date(2026, 5, 15), 19500.0),
        (date(2026, 10, 20), 42000.0),
        (date(2026, 11, 12), 44443.05),
    ]
    flows = [
        (date(2026, 5, 15), "SGD", 17000.0, None),
        (date(2026, 10, 20), "SGD", 22500.0, None),
        (date(2025, 12, 1), "SGD", 1000.0, None),  # before the opening close: ignored
    ]
    result = report(navs, flows)
    assert result.start == date(2025, 12, 31) and result.end == date(2026, 11, 12)
    assert result.opening_nav == pytest.approx(1993.70)
    assert result.net_contributions == pytest.approx(39500)
    assert result.net_profit == pytest.approx(2949.35)
    assert result.return_on_capital == pytest.approx(2949.35 / 41493.70)
    assert result.return_on_capital is not None and round(result.return_on_capital, 4) == 0.0711
    assert result.xirr is not None and result.twr is not None
    assert result.nav[-1].contributions == pytest.approx(41493.70)
    assert result.nav[-1].twr == pytest.approx(result.twr)
    assert [row.month for row in result.monthly] == ["2026-05", "2026-10", "2026-11"]
    assert sum(row.profit for row in result.monthly) == pytest.approx(2949.35)
    assert result.monthly[-1].end_nav == pytest.approx(44443.05)


def test_attribution_reconciles_to_net_profit():
    cash_lines = {
        "dividends": Converted(total=23.03),
        "interest_paid": Converted(total=-305.68),
        "commissions": Converted(total=-191.35),
        "fees": Converted(total=-28.63),
        "taxes": Converted(total=-17.22),
    }
    navs = [(date(2025, 12, 31), 1000.0), (date(2026, 3, 31), 2000.0)]
    result = report(navs, [(date(2026, 2, 1), "SGD", 500.0, None)], cash_lines=cash_lines)
    lines = {line.key: line.amount for line in result.attribution}
    assert lines["net_profit"] == pytest.approx(500)
    assert lines["investment"] == pytest.approx(500 - 23.03 + 305.68 + 191.35 + 28.63 + 17.22)
    booked = sum(amount for key, amount in lines.items() if key != "net_profit")
    assert booked == pytest.approx(lines["net_profit"])
    assert result.attribution[0].key == "investment" and result.attribution[-1].key == "net_profit"


def test_foreign_flows_convert_at_their_own_rate_then_the_broker_rate():
    book = RateBook(
        base="SGD", rates={"USD": [(date(2026, 1, 10), 1.30), (date(2026, 2, 1), 1.35)]}
    )
    converted = convert(
        book,
        [
            (date(2026, 1, 20), "USD", 100.0, None),  # 1.30, the rate on or before
            (date(2026, 2, 5), "USD", 100.0, 1.32),  # the row's own rate wins
            (date(2026, 1, 1), "USD", 100.0, None),  # only a later rate: approximate
            (date(2026, 1, 20), "EUR", 50.0, None),  # no rate: left out
            (date(2026, 1, 20), "SGD", 10.0, None),
        ],
    )
    assert converted.total == pytest.approx(130 + 132 + 130 + 10)
    assert converted.approximate == 1
    assert dict(converted.missing) == {"EUR": 50.0}


def test_unconvertible_and_late_flows_are_warned_not_guessed():
    navs = [(date(2025, 12, 31), 1000.0), (date(2026, 1, 31), 1200.0)]
    flows = [(date(2026, 1, 15), "EUR", 100.0, None), (date(2026, 2, 3), "SGD", 50.0, None)]
    result = report(navs, flows)
    assert result.net_contributions == 0
    assert any("EUR" in warning for warning in result.warnings)
    assert any("after the last NAV date" in warning for warning in result.warnings)


def test_account_opened_during_the_period_starts_from_zero():
    navs = [(date(2026, 3, 2), 10500.0)]
    result = report(navs, [(date(2026, 3, 2), "SGD", 10000.0, None)])
    assert result.start is None and result.opening_nav == 0
    assert result.net_profit == pytest.approx(500)
    assert result.twr == pytest.approx(0.05)
    assert any("starting from zero" in warning for warning in result.warnings)


def test_no_nav_in_period_is_empty():
    result = report([(date(2025, 12, 31), 1000.0)], [])
    assert result.net_profit is None and result.nav == []


class Client:
    def __init__(self):
        self.calls = []
        self.change_in_nav: list[dict[str, str]] = []
        self.conversion: list[dict[str, str]] = []
        self.gateway_after: list[dict[str, object]] = []

    def query(self, sql, parameters=None, **_kwargs):
        self.calls.append((sql, parameters or {}))
        if "section = 'ChangeInNAV'" in sql:
            values = self.change_in_nav
        elif "section = 'ConversionRates'" in sql:
            values = self.conversion
        elif "FROM ref.app_users" in sql:
            values = [{"user_id": USER, "display_name": "A", "active": True}]
        elif "FROM ref.broker_account_owners" in sql:
            values = [
                {"broker_code": "ibkr", "account_id": "U1", "user_id": USER, "simulated": False}
            ]
        elif "FROM ref.broker_accounts" in sql:
            values = [
                {
                    "broker_code": "ibkr",
                    "account_id": "U1",
                    "account_mode": "live",
                    "base_currency": "SGD",
                }
            ]
        elif "GROUP BY broker_code, account_id" in sql:
            values = []
        elif "AS nav" in sql:
            values = [
                {"d": date(2025, 12, 31), "nav": Decimal("1000.000000"), "flex": 1},
                {"d": date(2026, 6, 27), "nav": Decimal("9999.000000"), "flex": 0},
                {"d": date(2026, 6, 30), "nav": Decimal("2200.000000"), "flex": 1},
                *self.gateway_after,
            ]
        elif "metric = 'ExchangeRate'" in sql:
            values = [{"d": date(2026, 1, 2), "currency": "USD", "rate": Decimal("1.300000")}]
        elif "FROM broker.cash_flows" in sql:
            values = [
                {
                    "event_time": datetime(2026, 3, 2, 15, tzinfo=UTC),
                    "cash_flow_type": "deposit",
                    "amount": Decimal(1000),
                    "currency": b"SGD",  # FixedString(3) arrives as bytes
                },
                {
                    "event_time": datetime(2026, 4, 1, 15, tzinfo=UTC),
                    "cash_flow_type": "interest",
                    "amount": Decimal(-10),
                    "currency": b"USD",
                },
                {
                    "event_time": datetime(2026, 4, 1, 15, tzinfo=UTC),
                    "cash_flow_type": "dividend",
                    "amount": Decimal(5),
                    "currency": "SGD",
                },
                {
                    "event_time": datetime(2026, 4, 1, 15, tzinfo=UTC),
                    "cash_flow_type": "fx_conversion",
                    "amount": Decimal(-999),
                    "currency": "USD",
                },
            ]
        elif "FROM broker.executions" in sql:
            values = [
                {
                    "exec_time": datetime(2026, 2, 2, 15, tzinfo=UTC),
                    "commission": Decimal(2),
                    "commission_ccy": "USD",
                }
            ]
        else:
            values = []
        columns = list(values[0]) if values else []
        return SimpleNamespace(
            column_names=columns,
            result_rows=[tuple(item[name] for name in columns) for item in values],
        )


@pytest.fixture
def api():
    db = Client()
    app.dependency_overrides[repository] = lambda: PortfolioRepository(db)
    yield TestClient(app), db
    app.dependency_overrides.pop(repository, None)


def test_endpoint_builds_the_report_from_broker_tables(api):
    client, db = api
    response = client.get(
        f"/hub/api/v1/portfolio/accounts/ibkr/U1/performance?user_id={USER}&since=2026-01-01"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "SGD"
    assert body["net_contributions"] == pytest.approx(1000)
    assert body["net_profit"] == pytest.approx(200)
    lines = {line["key"]: line["amount"] for line in body["attribution"]}
    assert lines["interest_paid"] == pytest.approx(-13)
    assert lines["commissions"] == pytest.approx(-2.6)
    assert lines["dividends"] == pytest.approx(5)
    assert lines["investment"] == pytest.approx(200 + 13 + 2.6 - 5)
    sql = next(sql for sql, _ in db.calls if "AS nav" in sql)
    assert "endsWith(source_channel, 'flex_statement')" in sql and "America/New_York" in sql


def test_endpoint_rejects_a_future_start(api):
    client, _ = api
    assert (
        client.get(
            "/hub/api/v1/portfolio/accounts/ibkr/U1/performance?since=2999-01-01"
        ).status_code
        == 422
    )


# IBKR's change in NAV, 1 Jan - 12 Nov 2026, from the pasted activity statement (SGD).
STATEMENT = {
    "mtm": 5328.13,
    "dividends": 23.03,
    "changeInDividendAccruals": -0.09,
    "interest": -124.79,
    "changeInInterestAccruals": -180.89,
    "commissions": -191.35,
    "transactionTax": -28.63,
    "salesTax": -17.22,
    "fxTranslation": -0.55,
}


def split_days(total: dict[str, float], start: float, deposits: float) -> list[dict[str, float]]:
    """Two days whose lines sum to ``total``, with consistent starting/ending values."""
    first = {name: value / 2 for name, value in total.items()}
    profit = sum(first.values())
    day1 = {**first, "startingValue": start, "depositsWithdrawals": deposits, "twr": 1.0}
    day1["endingValue"] = start + deposits + profit
    day2 = {**first, "startingValue": day1["endingValue"], "depositsWithdrawals": 0.0, "twr": 2.0}
    day2["endingValue"] = day1["endingValue"] + profit
    return [day1, day2]


def test_ibkr_attribution_matches_the_statement():
    days = split_days(STATEMENT, 1993.70, 39500)
    profit = days[-1]["endingValue"] - 1993.70 - 39500
    lines, twr, financing = ibkr_attribution(days, profit)
    amounts = {line.key: line.amount for line in lines}
    assert amounts["investment"] == pytest.approx(5328.13)
    assert amounts["interest"] + amounts["interest_accruals"] == pytest.approx(-305.68)
    assert financing == pytest.approx(-305.68)
    assert amounts["fees"] == pytest.approx(-28.63) and amounts["taxes"] == pytest.approx(-17.22)
    assert amounts["net_profit"] == pytest.approx(4807.64)  # the statement rounds to 4,807.63
    assert "other" not in amounts and "unreconciled" not in amounts
    assert twr == pytest.approx(1.01 * 1.02 - 1)


def test_ibkr_attribution_names_what_it_cannot_explain():
    days = split_days({"mtm": 100.0}, 1000, 0)
    days[0]["costAdjustments"] = 0  # unknown fields are ignored, not double counted
    days[0]["endingValue"] += 7  # IBKR booked 7 outside the listed lines
    days[1]["startingValue"] += 7
    days[1]["endingValue"] += 7
    lines, _, _ = ibkr_attribution(days, 107 + 3)
    amounts = {line.key: line.amount for line in lines}
    assert amounts["other"] == pytest.approx(7)
    assert amounts["unreconciled"] == pytest.approx(3)
    assert sum(amount for key, amount in amounts.items() if key != "net_profit") == pytest.approx(
        110
    )


def test_ibkr_attribution_falls_back_to_realized_and_unrealized_without_mtm():
    lines, _, _ = ibkr_attribution(
        [{"startingValue": 100, "endingValue": 130, "realized": 10, "changeInUnrealized": 20}], 30
    )
    assert {line.key: line.amount for line in lines}["investment"] == pytest.approx(30)


def test_report_uses_ibkr_lines_only_when_every_nav_day_is_covered():
    navs = [(date(2025, 12, 31), 1000.0), (date(2026, 1, 2), 1100.0), (date(2026, 1, 5), 1150.0)]

    def day(start: float, end: float, twr: float) -> dict[str, float]:
        return {"startingValue": start, "endingValue": end, "mtm": end - start, "twr": twr}

    covered = {date(2026, 1, 2): day(1000, 1100, 10.0), date(2026, 1, 5): day(1100, 1150, 50 / 11)}
    full = build(
        currency="SGD",
        since=SINCE,
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
        ibkr_days=covered,
    )
    assert full.attribution_source == "ibkr"
    assert full.ibkr_twr == pytest.approx(full.twr)
    assert {line.key: line.amount for line in full.attribution}["investment"] == pytest.approx(150)

    partial = build(
        currency="SGD",
        since=SINCE,
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
        ibkr_days={date(2026, 1, 5): covered[date(2026, 1, 5)]},
    )
    # A gap inside the statement history falls back to cash records.
    assert partial.attribution_source == "cash" and partial.ibkr_twr is None
    assert any("missing for 1 NAV date" in warning for warning in partial.warnings)

    trailing = build(
        currency="SGD",
        since=SINCE,
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
        ibkr_days={date(2026, 1, 2): covered[date(2026, 1, 2)]},
    )
    # Days after the last statement are named, not a reason to drop IBKR's lines.
    assert trailing.attribution_source == "ibkr"
    pending = {line.key: line.amount for line in trailing.attribution}["pending"]
    assert pending == pytest.approx(50)


def test_report_flags_deposits_that_disagree_with_ibkr():
    navs = [(date(2025, 12, 31), 1000.0), (date(2026, 1, 2), 2000.0)]
    ibkr = {
        date(2026, 1, 2): {
            "startingValue": 1000,
            "endingValue": 2000,
            "depositsWithdrawals": 1000,
            "twr": 0.0,
        }
    }
    result = build(
        currency="SGD",
        since=SINCE,
        nav_rows=navs,
        flow_rows=[],
        cash_lines={},
        book=RateBook(base="SGD"),
        ibkr_days=ibkr,
    )
    assert any("IBKR statements show 1,000.00 SGD" in warning for warning in result.warnings)
    assert {line.key: line.amount for line in result.attribution}["unreconciled"] == pytest.approx(
        1000
    )


def test_endpoint_prefers_ibkr_change_in_nav_and_statement_rates(api):
    client, db = api
    db.conversion = [{"d": "20260401", "currency": "USD", "rate": "1.25"}]
    db.change_in_nav = [
        {
            "d": "20260630",
            "startingValue": "1000",
            "endingValue": "2200",
            "depositsWithdrawals": "1000",
            "mtm": "215",
            "interest": "-12.5",
            "changeInInterestAccruals": "-2.5",
            "twr": "18.4",
        }
    ]
    body = client.get(f"/hub/api/v1/portfolio/accounts/ibkr/U1/performance?user_id={USER}").json()
    assert body["attribution_source"] == "ibkr"
    assert body["ibkr_twr"] == pytest.approx(0.184)
    assert body["financing_cost"] == pytest.approx(-15)
    lines = {line["key"]: line["amount"] for line in body["attribution"]}
    assert lines["investment"] == pytest.approx(215) and lines["interest"] == pytest.approx(-12.5)
    assert "unreconciled" not in lines
    _, params = next(call for call in db.calls if "section = 'ConversionRates'" in call[0])
    assert params["wanted"] == ["USD"] and params["base"] == "SGD"


def test_statement_rate_wins_over_the_gateway_rate_in_cash_attribution(api):
    client, db = api
    db.conversion = [{"d": "20260401", "currency": "USD", "rate": "1.25"}]
    body = client.get(f"/hub/api/v1/portfolio/accounts/ibkr/U1/performance?user_id={USER}").json()
    lines = {line["key"]: line["amount"] for line in body["attribution"]}
    assert body["attribution_source"] == "cash"
    assert lines["interest_paid"] == pytest.approx(-12.5)  # 1.25 on 1 April, not the Gateway 1.30
    assert lines["commissions"] == pytest.approx(-2.6)  # 2 February: only the Gateway rate exists


def test_gateway_nav_only_extends_past_the_last_statement(api):
    client, db = api
    # The Saturday Gateway reading (27 June, 9,999) sits inside the statement history: ignored.
    db.gateway_after = [{"d": date(2026, 7, 2), "nav": Decimal("2300.000000"), "flex": 0}]
    db.change_in_nav = [
        {
            "d": "20260630",
            "startingValue": "1000",
            "endingValue": "2200",
            "depositsWithdrawals": "1000",
            "mtm": "215",
            "interest": "-15",
            "twr": "18.4",
        }
    ]
    body = client.get(f"/hub/api/v1/portfolio/accounts/ibkr/U1/performance?user_id={USER}").json()
    assert [point["date"] for point in body["nav"]] == ["2026-06-30", "2026-07-02"]
    assert body["end"] == "2026-07-02" and body["net_profit"] == pytest.approx(300)
    assert body["attribution_source"] == "ibkr"
    lines = {line["key"]: line for line in body["attribution"]}
    assert lines["pending"]["amount"] == pytest.approx(100)
    assert "2026-06-30" in lines["pending"]["note"]
    assert "unreconciled" not in lines


def test_history_starting_inside_the_period_measures_from_the_first_close():
    navs = [(date(2025, 12, 31), 1993.70), (date(2026, 1, 2), 2093.70)]
    result = report(navs, [], since=date(2025, 11, 19))
    assert result.start == date(2025, 12, 31) and result.opening_nav == pytest.approx(1993.70)
    assert result.net_profit == pytest.approx(100)  # the opening balance is not profit
    assert any("history starts on 2025-12-31" in warning for warning in result.warnings)
