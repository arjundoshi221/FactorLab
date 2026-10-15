from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from factorlab.components.api.portfolio_pnl import ledger, summarize

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
TAGS = {"theme_id": "theme", "strategy_id": "strategy", "trade_name_id": "name"}


def fill(exec_id, side, quantity, price, **extra):
    return {
        "broker_code": "test",
        "account_id": "A",
        "account_mode": "paper",
        "exec_id": exec_id,
        "exec_time": NOW - timedelta(minutes=10),
        "perm_id": 1,
        "vendor_id": "100",
        "trading_symbol": "ABC",
        "currency": "USD",
        "product_type": "common",
        "side": side,
        "quantity": quantity,
        "price": price,
        "commission": "1",
        "commission_ccy": "USD",
        "version": 1,
        **TAGS,
        **extra,
    }


def calculate(fills, position, price: int | str | Decimal | None = 120, **extra):
    snapshot = {
        "broker_code": "test",
        "account_id": "A",
        "account_mode": "paper",
        "vendor_id": "100",
        "position": position,
        "currency": "USD",
        "market_price": price,
        "snapshot_time": NOW,
        **extra,
    }
    return ledger(fills, [snapshot] if position else [], {("test", "A"): NOW}, now=NOW)


def test_fifo_partial_close_expenses_each_commission_once():
    rows = calculate(
        [fill("1", "BUY", 10, 100), fill("2", "BUY", 10, 110), fill("3", "SELL", 15, 130)], 5
    )
    row = rows[0]
    assert Decimal(row["realized"]) == 400
    assert Decimal(row["unrealized"]) == 50
    assert Decimal(row["commissions"]) == 3
    assert Decimal(row["total"]) == 447
    assert Decimal(row["quantity"]) == 5


@pytest.mark.parametrize(
    ("fills", "position", "realized", "unrealized"),
    [
        ([fill("1", "SSHORT", 10, 130), fill("2", "BUY", 4, 110)], -6, 80, 60),
        ([fill("1", "BUY", 10, 100), fill("2", "SELL", 15, 130)], -5, 300, 50),
        ([fill("1", "SSHORT", 10, 130), fill("2", "BUY", 15, 100)], 5, 300, 100),
    ],
)
def test_shorts_and_reversals(fills, position, realized, unrealized):
    row = calculate(fills, position)[0]
    assert Decimal(row["realized"]) == realized
    assert Decimal(row["unrealized"]) == unrealized


def test_same_instrument_different_tag_triples_never_match():
    rows = calculate([fill("1", "BUY", 10, 100), fill("2", "SELL", 5, 130, theme_id="other")], 5)
    assert [Decimal(row["realized"]) for row in rows] == [0, 0]
    assert [Decimal(row["quantity"]) for row in rows] == [10, -5]
    assert [Decimal(row["unrealized"]) for row in rows] == [200, 50]
    assert len(summarize(rows, "theme_id")) == 2
    strategy = summarize(rows, "strategy_id")[0]
    assert Decimal(strategy["total"]) == 248


def test_late_correction_dedup_and_retagging_restate_history():
    opening = fill("1", "BUY", 10, 100)
    closing = fill("2", "SELL", 5, 130)
    correction = {**closing, "price": 140, "version": 2}
    row = calculate([closing, opening, correction], 5)[0]
    assert Decimal(row["realized"]) == 200
    assert Decimal(row["commissions"]) == 2
    moved = calculate([{**row, "trade_name_id": "new"} for row in [opening, correction]], 5)
    assert summarize(moved, "trade_name_id")[0]["id"] == "new"
    assert moved[0]["total"] == row["total"]


def test_currencies_and_cross_currency_commissions_are_separate():
    rows = calculate([fill("1", "BUY", 10, 100, commission_ccy="INR", commission=5)], 10)
    usd, inr = rows
    assert usd["currency"] == "USD" and Decimal(usd["total"]) == 200
    assert inr["currency"] == "INR" and Decimal(inr["total"]) == -5


@pytest.mark.parametrize(
    ("extra", "issue"),
    [
        ({"commission": None}, "missing_commission"),
        ({"commission_ccy": ""}, "missing_commission_currency"),
        ({"product_type": "option"}, "unsupported_instrument"),
        ({"quantity": "NaN"}, "invalid_fill"),
    ],
)
def test_unknown_data_never_becomes_a_zero_total(extra, issue):
    row = calculate([{**fill("1", "BUY", 10, 100), **extra}], 10)[0]
    assert row["total"] is None
    assert issue in row["issues"]


def test_missing_prices_and_history_are_unavailable():
    row = calculate([fill("1", "BUY", 10, 100)], 10, price=None)[0]
    assert row["unrealized"] is None and row["total"] is None
    assert "missing_price" in row["issues"]
    row = calculate([fill("1", "BUY", 10, 100)], 20)[0]
    assert row["realized"] is None and row["unrealized"] is None
    assert "incomplete_history" in row["issues"]
    row = calculate([], 10)[0]
    assert row["total"] is None and row["quantity"] == "10"


def test_successful_empty_snapshot_detects_unclosed_recorded_holdings():
    row = calculate([fill("1", "BUY", 10, 100)], 0)[0]
    assert row["total"] is None
    assert "incomplete_history" in row["issues"]


def test_snapshot_cutoff_avoids_false_drift_for_new_fills():
    snapshot = {
        "broker_code": "test",
        "account_id": "A",
        "account_mode": "paper",
        "vendor_id": "100",
        "position": 10,
        "currency": "USD",
        "market_price": 120,
        "snapshot_time": NOW - timedelta(minutes=5),
    }
    rows = ledger(
        [fill("1", "BUY", 10, 100), fill("2", "SELL", 5, 130, exec_time=NOW)],
        [snapshot],
        {("test", "A"): snapshot["snapshot_time"]},
        now=NOW,
    )
    assert "incomplete_history" not in rows[0]["issues"]
    assert Decimal(rows[0]["total"]) == 248


def test_stale_marks_and_modes_and_simulation_are_explicit():
    row = calculate(
        [fill("1", "BUY", 10, 100, exec_time=NOW - timedelta(days=2))],
        10,
        snapshot_time=NOW - timedelta(days=1),
    )[0]
    assert "stale_price" in row["issues"]
    assert (
        len(
            summarize(
                [row, {**row, "account_mode": "live"}, {**row, "simulated": True}], "theme_id"
            )
        )
        == 3
    )


def test_account_scoped_execution_ids_are_not_deduplicated_together():
    rows = ledger(
        [fill("1", "BUY", 10, 100), fill("1", "BUY", 10, 100, account_id="B")], [], {}, now=NOW
    )
    assert len(rows) == 2


def test_decimal_precision_is_preserved():
    row = calculate(
        [fill("1", "BUY", "0.000001", "100.000001", commission=0)],
        Decimal("0.000001"),
        price="100.000002",
    )[0]
    assert Decimal(row["unrealized"]) == Decimal("0.000000000001")


def test_large_decimal_products_and_aggregate_sums_keep_fractional_digits():
    row = calculate(
        [fill("1", "BUY", "99999999999999.999999", "0.000001", commission=0)],
        Decimal("99999999999999.999999"),
        price="99999999999999.999999",
    )[0]
    assert Decimal(row["unrealized"]) == Decimal("9999999999999999999700000000.000000000002")
    grouped = summarize([row, row], "theme_id")[0]
    assert Decimal(grouped["unrealized"]) == Decimal("19999999999999999999400000000.000000000004")


def test_unsupported_instruments_keep_the_recorded_quantity_visible():
    row = calculate([fill("1", "BUY", 10, 100, product_type="future")], 10)[0]
    assert row["quantity"] == "10" and row["total"] is None
