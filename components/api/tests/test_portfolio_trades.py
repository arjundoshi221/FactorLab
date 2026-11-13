"""Open/closed trades: flat-to-flat round trips, FIFO realized, marks and explicit unknowns."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, SnapshotPage, repository
from factorlab.components.api.portfolio_trades import build_trades, summarize

USER = "d677331a-a04f-454a-8a1e-02425be2c101"
THEME = "11111111-1111-1111-1111-111111111111"
STRATEGY = "22222222-2222-2222-2222-222222222222"
NAME = "33333333-3333-3333-3333-333333333333"
NAMES = {
    "theme_id": {THEME: "AI"},
    "strategy_id": {STRATEGY: "Momentum"},
    "trade_name_id": {NAME: "NVDA swing"},
}
ACCOUNT = {
    "broker_code": "ibkr",
    "account_id": "U1",
    "user_id": USER,
    "simulated": False,
    "account_mode": "live",
}

NOW = datetime(2026, 10, 20, 15, tzinfo=UTC)
SNAPSHOT_AT = datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
TAGS = {"theme_id": THEME, "strategy_id": STRATEGY, "trade_name_id": NAME}


def fill(exec_id, quantity, price, side="BUY", at="2026-10-15T14:00:00", perm=1, **extra):
    return {
        "broker_code": "ibkr",
        "account_id": "U1",
        "account_mode": "live",
        "exec_id": exec_id,
        "perm_id": perm,
        "exec_time": datetime.fromisoformat(at).replace(tzinfo=UTC),
        "quantity": Decimal(str(quantity)),
        "price": Decimal(str(price)),
        "side": side,
        "trading_symbol": extra.pop("symbol", "NVDA"),
        "currency": "USD",
        "vendor_id": extra.pop("vendor_id", "4815747"),
        "listing_id": None,
        "security_id": None,
        "contract_id": None,
        "product_type": "STK",
        "commission": Decimal(str(extra.pop("commission", "1"))),
        "commission_ccy": extra.pop("commission_ccy", "USD"),
        "version": 1,
        **extra,
    }


def mark(position, price, vendor_id="4815747", symbol="NVDA"):
    return {
        "broker_code": "ibkr",
        "account_id": "U1",
        "account_mode": "live",
        "vendor_id": vendor_id,
        "trading_symbol": symbol,
        "position": Decimal(str(position)),
        "market_price": Decimal(str(price)),
        "currency": "USD",
        "product_type": "STK",
        "snapshot_time": SNAPSHOT_AT,
    }


TIMES: dict[tuple[str, str], datetime | None] = {("ibkr", "U1"): SNAPSHOT_AT}
NAMES_BY_ID = {k: v for names in NAMES.values() for k, v in names.items()}


def test_round_trip_closes_at_flat_and_a_reopen_starts_a_new_trade():
    rows = build_trades(
        [
            {**fill("a", 10, 100), **TAGS},
            {**fill("b", 10, 110, at="2026-10-15T15:00:00"), **TAGS},
            fill("c", 15, 120, "SELL", at="2026-10-16T14:00:00", perm=2),
            fill("d", 5, 90, "SELL", at="2026-10-17T14:00:00", perm=3),
            fill("e", 4, 95, at="2026-10-18T14:00:00", perm=4),
        ],
        [mark(4, 100)],
        TIMES,
        NAMES,
        now=NOW,
    )
    closed, reopened = rows
    assert (closed.status, closed.direction) == ("closed", "long")
    assert closed.closed_at == datetime(2026, 10, 17, 14, tzinfo=UTC)
    assert closed.entry_quantity == closed.max_quantity == "20"
    assert Decimal(closed.avg_entry_price) == 105
    assert Decimal(closed.avg_exit_price or "0") == Decimal("112.5")
    # FIFO: 10 @100 and 5 @110 close at 120; 5 @110 close at 90.
    assert Decimal(closed.realized or "x") == 200 + 50 - 100
    assert Decimal(closed.net or "x") == 150 - 4
    assert closed.return_pct == "0.071429"
    assert [f["role"] for f in closed.fills] == ["open", "add", "reduce", "close"]
    assert (closed.theme_name, closed.tags_mixed, closed.orders) == ("AI", True, ["1", "2", "3"])
    assert (reopened.status, reopened.quantity, reopened.closed_at) == ("open", "4", None)
    assert Decimal(reopened.unrealized or "x") == 20
    assert reopened.market_price == "100" and reopened.issues == []


def test_a_fill_crossing_zero_closes_one_trade_and_opens_the_next_with_split_fees():
    long, short = build_trades(
        [fill("a", 10, 100), fill("b", 15, 110, "SELL", perm=2, commission="3")],
        [mark(-5, 100)],
        TIMES,
        NAMES,
        now=NOW,
    )
    assert long.status == "closed" and Decimal(long.realized or "x") == 100
    assert Decimal(long.commissions[0]["amount"]) == 1 + 2
    assert (short.direction, short.status, short.quantity) == ("short", "open", "-5")
    assert Decimal(short.commissions[0]["amount"]) == 1
    assert Decimal(short.unrealized or "x") == 50
    assert short.fills[0]["role"] == "open" and short.fills[0]["quantity"] == "5"


def test_unknown_inputs_make_pnl_unavailable_never_zero():
    rows = build_trades(
        [
            # Sold shares bought before recorded history: looks short, does not reconcile.
            fill("a", 5, 100, "SELL"),
            fill("b", 2, 50, vendor_id="9", symbol="AMD", commission="0", commission_ccy=None),
        ],
        [mark(0, 100), mark(2, 55, vendor_id="9", symbol="AMD")],
        TIMES,
        NAMES,
        now=NOW,
    )
    nvda, amd = rows
    assert "incomplete_history" in nvda.issues
    assert nvda.realized is None and nvda.unrealized is None and nvda.net is None
    assert "missing_commission" in amd.issues
    assert Decimal(amd.unrealized or "x") == 10 and amd.net is None
    stale = build_trades(
        [fill("a", 1, 1)], [mark(1, 2)], TIMES, NAMES, now=datetime(2026, 11, 1, tzinfo=UTC)
    )
    assert "stale_price" in stale[0].issues


def test_summary_counts_closed_trades_per_currency_and_mode():
    rows = build_trades(
        [
            fill("a", 1, 100),
            fill("b", 1, 110, "SELL", at="2026-10-16T14:00:00"),
            fill("c", 1, 100, at="2026-10-17T14:00:00"),
            fill("d", 1, 95, "SELL", at="2026-10-18T14:00:00"),
            fill("e", 1, 100, at="2026-10-19T14:00:00"),
        ],
        [mark(1, 100)],
        TIMES,
        NAMES,
        now=NOW,
    )
    [summary] = summarize(rows)
    assert (summary.closed, summary.wins, summary.losses) == (2, 1, 1)
    assert Decimal(summary.realized) == 5 and Decimal(summary.net or "x") == 1


class TradesRepo(PortfolioRepository):
    def __init__(self, fills, assignments, positions):
        super().__init__(None)
        self.fills, self.assignments, self.positions = fills, assignments, positions

    def profiles(self):
        return [{"user_id": USER, "display_name": "Jai", "active": True}]

    def accounts(self, user_id=""):
        return [ACCOUNT]

    def account(self, broker_code, account_id, user_id=""):
        return ACCOUNT

    def query(self, sql, parameters=None):
        catalogs = {"book.themes": THEME, "book.strategies": STRATEGY, "book.trade_names": NAME}
        for table, entry in catalogs.items():
            if f"FROM {table}" in sql:
                return [{"id": entry, "name": NAMES_BY_ID[entry]}]
        if "order_assignment_events" in sql:
            return [
                {"perm_id": perm, "assignment": (*tags, "e", datetime(2026, 10, 1, tzinfo=UTC))}
                for perm, tags in self.assignments.items()
            ]
        if "broker.executions" in sql:
            return self.fills
        return []

    def snapshot(self, account, dataset):
        return SnapshotPage(
            items=[p for p in self.positions if p["account_id"] == account["account_id"]],
            snapshot_time=SNAPSHOT_AT,
            age_seconds=0,
            source_channel="test",
            simulated=False,
            freshness="fresh",
        )


@pytest.fixture
def trades_api():
    fills = [
        fill("a", 1, 100, at="2026-10-14T14:00:00"),
        fill("b", 1, 110, "SELL", at="2026-10-15T14:00:00", perm=2),
        fill("c", 2, 50, at="2026-10-16T14:00:00", perm=3, vendor_id="9", symbol="AMD"),
    ]
    repo = TradesRepo(
        fills,
        {1: (THEME, STRATEGY, NAME)},
        [mark(2, 55, vendor_id="9", symbol="AMD")],
    )
    app.dependency_overrides[repository] = lambda: repo
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(repository)


def test_trades_endpoint_filters_status_and_tags_before_paging(trades_api):
    url = "/hub/api/v1/portfolio/trades"
    body = trades_api.get(url, params={"user_id": USER}).json()
    assert body["total"] == 2 and body["counts"] == {"open": 1, "closed": 1}
    assert [row["trading_symbol"] for row in body["items"]] == ["AMD", "NVDA"]
    assert body["summary"][0]["closed"] == 1 and body["summary"][0]["wins"] == 1
    closed = trades_api.get(url, params={"user_id": USER, "status": "closed"}).json()
    assert [row["theme_name"] for row in closed["items"]] == ["AI"]
    assert closed["counts"] == {"open": 1, "closed": 1}
    tagged = trades_api.get(url, params={"user_id": USER, "theme_id": THEME}).json()
    assert tagged["total"] == 1
    later = trades_api.get(url, params={"user_id": USER, "start": "2026-10-15T15:00:00Z"}).json()
    assert [row["trading_symbol"] for row in later["items"]] == ["AMD"]
    assert trades_api.get(url, params={"user_id": USER, "status": "maybe"}).status_code == 422
    assert trades_api.get(url, params={"user_id": USER, "broker_code": "ibkr"}).status_code == 422
