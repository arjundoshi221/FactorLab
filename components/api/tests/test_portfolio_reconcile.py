from datetime import UTC, date, datetime
from decimal import Decimal

from factorlab.components.api.portfolio_pnl import identity, ledger, summarize
from factorlab.components.api.portfolio_reconcile import (
    reconcile,
    statement,
    statement_meta,
    with_broker_pnl,
)

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
EARNINGS = {"theme_id": "earnings", "strategy_id": "s", "trade_name_id": "n"}
AI_INFRA = {"theme_id": "ai", "strategy_id": "s", "trade_name_id": "n"}
HEADER = {"generated_at": datetime(2026, 10, 15, 2, tzinfo=UTC), "statement_to": date(2026, 10, 14)}


def fill(exec_id, perm, side, quantity, price, tags, day, realized=None, **extra):
    return {
        "broker_code": "ibkr",
        "account_id": "U1",
        "account_mode": "live",
        "simulated": False,
        "exec_id": exec_id,
        "exec_time": datetime(2026, 10, day, 15, tzinfo=UTC),
        "perm_id": perm,
        "vendor_id": "265598",
        "trading_symbol": "AAPL",
        "currency": "USD",
        "product_type": "STK",
        "side": side,
        "quantity": quantity,
        "price": price,
        "commission": "1",
        "commission_ccy": "USD",
        "realized_pnl": realized,
        "version": 1,
        **tags,
        **extra,
    }


# Buy 10 @100 for Earnings, 10 @150 for AI Infra, then sell the AI Infra order @160.
FILLS = [
    fill("1", 1, "BUY", 10, 100, EARNINGS, 1, realized="0"),
    fill("2", 2, "BUY", 10, 150, AI_INFRA, 2, realized="0"),
    # IBKR closes the oldest lot: 10 * (160 - 100) - 1 opening - 1 closing commission.
    fill("3", 3, "SELL", 10, 160, AI_INFRA, 3, realized="598"),
]
MARK = {
    "broker_code": "ibkr",
    "account_id": "U1",
    "account_mode": "live",
    "simulated": False,
    "vendor_id": "265598",
    "product_type": "STK",
    "position": 10,
    "currency": "USD",
    "market_price": 170,
    "snapshot_time": NOW,
}
ORDER_TAGS: dict[tuple[str, str, int], tuple[str | None, ...]] = {
    ("ibkr", "U1", 1): ("earnings", "s", "n"),
    ("ibkr", "U1", 2): ("ai", "s", "n"),
    ("ibkr", "U1", 3): ("ai", "s", "n"),
}


def position(quantity="10", cost="1501", level="SUMMARY", perm=""):
    return {
        "level_of_detail": level,
        "vendor_id": "265598",
        "trading_symbol": "AAPL",
        "currency": "USD",
        "asset_category": "STK",
        "position": quantity,
        "cost_basis": cost,
        "perm_id": perm,
    }


def report(fills=FILLS, positions=None, mark=MARK, header=HEADER, since=None):
    rows = [position()] if positions is None else positions
    statements = {("ibkr", "U1"): statement(header, rows)}
    snapshots = [mark] if mark else []
    marks = {identity(row): row for row in snapshots}
    times: dict[tuple[str, str], datetime | None] = {("ibkr", "U1"): NOW}
    details = ledger(fills, snapshots, times, now=NOW, since=since)
    everything = ledger(fills, snapshots, times, now=NOW)
    annotated = with_broker_pnl(details, fills, statements, marks, ORDER_TAGS, since=since)
    return (
        {row["theme_id"]: row for row in annotated},
        reconcile(everything, fills, statements, marks),
        statements,
    )


def test_ibkr_realized_follows_its_own_lots_and_unrealized_the_open_holder():
    rows, reconciliation, _ = report()
    ai, earnings = rows["ai"], rows["earnings"]
    # Tagged FIFO: AI Infra made 100 gross; Earnings still holds the 100 lot.
    assert (Decimal(ai["total"]), Decimal(earnings["total"])) == (98, 699)
    # IBKR closed the Earnings lot, so its realized PnL lands on the AI Infra sell...
    assert Decimal(ai["ibkr_realized"]) == 598
    assert Decimal(ai["ibkr_unrealized"]) == 0
    # ...and its open position (the 150 lot, opening commission in the basis) on Earnings.
    assert Decimal(earnings["ibkr_unrealized"]) == 1700 - 1501
    assert Decimal(earnings["ibkr_total"]) == 199
    # The instrument total does not depend on lot matching: both sides agree.
    (row,) = reconciliation
    assert row["status"] == "match"
    assert Decimal(row["ours"]["total"]) == Decimal(row["ibkr"]["total"]) == 797
    assert Decimal(row["difference"]) == 0


def test_lot_detail_attributes_each_open_lot_to_its_opening_order():
    fills = [*FILLS[:2], fill("3", 3, "SELL", 5, 160, AI_INFRA, 3, realized="298.5")]
    lots = [
        position("15", "2001.5"),
        position("5", "500.5", "LOT", "1"),
        position("10", "1501", "LOT", "2"),
    ]
    rows, reconciliation, statements = report(fills, lots, {**MARK, "position": 15})
    assert Decimal(rows["earnings"]["ibkr_unrealized"]) == 5 * 170 - Decimal("500.5")
    assert Decimal(rows["ai"]["ibkr_unrealized"]) == 10 * 170 - 1501
    assert reconciliation[0]["status"] == "match"
    assert statement_meta(statements)[0]["lot_detail"] is True


def test_open_position_split_across_tags_without_lots_is_unavailable_per_tag():
    fills = [*FILLS[:2], fill("3", 3, "SELL", 5, 160, AI_INFRA, 3, realized="298.5")]
    rows, reconciliation, _ = report(fills, [position("15", "2001.5")], {**MARK, "position": 15})
    for row in rows.values():
        assert row["ibkr_unrealized"] is None
        assert "ibkr_split_position" in row["ibkr_issues"]
    assert Decimal(rows["ai"]["ibkr_realized"]) == Decimal("298.5")
    # The instrument-level comparison still works.
    assert reconciliation[0]["status"] == "match"


def test_fills_after_the_statement_wait_for_the_next_one():
    late = fill(
        "4", 4, "BUY", 1, 165, AI_INFRA, 15, exec_time=datetime(2026, 10, 15, 10, tzinfo=UTC)
    )
    rows, reconciliation, _ = report([*FILLS, late], mark={**MARK, "position": 11})
    assert rows["ai"]["ibkr_realized"] is None
    assert "ibkr_awaiting_statement" in rows["ai"]["ibkr_issues"]
    assert reconciliation[0]["status"] == "ibkr_unavailable"
    assert "ibkr_awaiting_statement" in reconciliation[0]["reasons"]


def test_no_statement_and_mismatches_are_explicit():
    _, reconciliation, _ = report(header=None)
    assert reconciliation[0]["status"] == "ibkr_unavailable"
    assert "ibkr_no_statement" in reconciliation[0]["reasons"]

    _, reconciliation, _ = report(positions=[position("10", "1401")])
    (row,) = reconciliation
    assert row["status"] == "mismatch"
    assert Decimal(row["difference"]) == -100


def test_gateway_opening_fill_without_realized_counts_as_zero():
    fills = [{**FILLS[0], "realized_pnl": None}, *FILLS[1:]]
    _, reconciliation, _ = report(fills)
    assert reconciliation[0]["status"] == "match"


def test_period_ibkr_total_is_realized_in_the_window_and_groups_sum_it():
    since = datetime(2026, 10, 3, tzinfo=UTC)
    rows, _, _ = report(since=since)
    assert Decimal(rows["ai"]["ibkr_total"]) == 598
    assert Decimal(rows["earnings"]["ibkr_realized"]) == 0
    groups = {row["id"]: row for row in summarize(list(rows.values()), "theme_id", period=True)}
    assert Decimal(groups["ai"]["ibkr_total"]) == 598
    assert Decimal(groups["earnings"]["ibkr_total"]) == 0


def test_all_time_groups_sum_ibkr_values_and_name_exclusions():
    fills = [*FILLS[:2], fill("3", 3, "SELL", 5, 160, AI_INFRA, 3, realized="298.5")]
    rows, _, _ = report(fills, [position("15", "2001.5")], {**MARK, "position": 15})
    groups = {row["id"]: row for row in summarize(list(rows.values()), "theme_id")}
    assert groups["ai"]["ibkr_total"] is None
    assert [row["trading_symbol"] for row in groups["ai"]["ibkr_excluded"]] == ["AAPL"]
    assert "ibkr_split_position" in groups["ai"]["ibkr_issues"]


def test_transaction_taxes_are_costs_so_gst_on_commissions_reconciles():
    # GST of 9% on each 1.00 commission: IBKR puts it in cost basis and realized PnL.
    fills = [
        {**FILLS[0], "taxes": Decimal("0.09")},
        {**FILLS[1], "taxes": Decimal("0.09")},
        {**FILLS[2], "taxes": Decimal("0.09"), "realized_pnl": "597.82"},
    ]
    rows, reconciliation, _ = report(fills, [position("10", "1501.09")])
    assert Decimal(rows["ai"]["commissions"]) == Decimal("2.18")
    assert Decimal(rows["earnings"]["commissions"]) == Decimal("1.09")
    (row,) = reconciliation
    assert row["status"] == "match"
    assert Decimal(row["difference"]) == 0
