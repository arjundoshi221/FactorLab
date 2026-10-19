"""Trade ledger: full-history filters, exact decimals and the owner profile's tags only."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.portfolio import PortfolioRepository, repository
from factorlab.components.api.portfolio_ledger import build_orders

USER = "d677331a-a04f-454a-8a1e-02425be2c101"
THEME = "11111111-1111-1111-1111-111111111111"
STRATEGY = "22222222-2222-2222-2222-222222222222"
NAME = "33333333-3333-3333-3333-333333333333"
LISTING = "44444444-4444-4444-4444-444444444444"
SECURITY = "55555555-5555-5555-5555-555555555555"
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


def fill(
    exec_id,
    perm,
    quantity,
    price,
    *,
    side="BUY",
    at="2026-10-15T14:00:00",
    account="U1",
    version=1,
    **extra,
):
    return {
        "broker_code": "ibkr",
        "account_id": account,
        "account_mode": "live",
        "exec_id": exec_id,
        "perm_id": perm,
        "exec_time": datetime.fromisoformat(at).replace(tzinfo=UTC),
        "quantity": Decimal(str(quantity)),
        "price": Decimal(str(price)),
        "side": side,
        "trading_symbol": extra.pop("symbol", "NVDA"),
        "currency": extra.pop("currency", "USD"),
        "listing_id": extra.pop("listing_id", LISTING),
        "security_id": extra.pop("security_id", SECURITY),
        "contract_id": None,
        "product_type": "STK",
        "commission": extra.pop("commission", Decimal("1.0")),
        "commission_ccy": extra.pop("commission_ccy", "USD"),
        "realized_pnl": extra.pop("realized_pnl", Decimal(0)),
        "version": version,
        **extra,
    }


def test_orders_group_fills_with_exact_prices_and_separate_currencies():
    rows = build_orders(
        ACCOUNT,
        [
            {
                **fill("a", 7, "1", "100.10"),
                "theme_id": THEME,
                "strategy_id": STRATEGY,
                "trade_name_id": NAME,
            },
            {
                **fill("b", 7, "2", "100.25", at="2026-10-15T14:01:00", commission_ccy="EUR"),
                "theme_id": THEME,
                "strategy_id": STRATEGY,
                "trade_name_id": NAME,
            },
            fill("c", 0, "5", "10", side="SELL"),
            fill("d", 0, "5", "10", side="SELL", realized_pnl=None),
        ],
        NAMES,
    )
    order = next(r for r in rows if r.perm_id == "7")
    assert order.quantity == "3"
    assert Decimal(order.avg_price or "0") == Decimal("300.60") / 3
    assert order.fill_count == 2 and order.tag_status == "tagged"
    assert (order.theme_name, order.strategy_name, order.trade_name_name) == (
        "AI",
        "Momentum",
        "NVDA swing",
    )
    assert order.commissions == [
        {"currency": "EUR", "amount": "1.0"},
        {"currency": "USD", "amount": "1.0"},
    ]
    # Fills without a durable order ID are separate, untaggable rows.
    loose = [r for r in rows if r.perm_id == "0"]
    assert len(loose) == 2 and all(r.tag_status == "not_taggable" for r in loose)
    assert {r.broker_realized_pnl for r in loose} == {"0", None}


def test_mixed_sides_are_labelled():
    rows = build_orders(ACCOUNT, [fill("a", 9, 1, 1), fill("b", 9, 1, 1, side="SELL")], NAMES)
    assert rows[0].side == "MIXED"


class FakeRepo(PortfolioRepository):
    def __init__(self, accounts, fills, assignments):
        super().__init__(None)
        self._accounts = accounts
        self._fills = fills
        self._assignments = assignments
        self.sql = []

    def profiles(self):
        return [{"user_id": USER, "display_name": "Jai", "active": True}]

    def accounts(self, user_id=""):
        return self._accounts

    def account(self, broker_code, account_id, user_id=""):
        return next(
            a
            for a in self._accounts
            if (a["broker_code"], a["account_id"]) == (broker_code, account_id)
        )

    def query(self, sql, parameters=None):
        self.sql.append(sql)
        params = parameters or {}
        if "FROM book.themes" in sql:
            return [{"id": THEME, "name": "AI"}]
        if "FROM book.strategies" in sql:
            return [{"id": STRATEGY, "name": "Momentum"}]
        if "FROM book.trade_names" in sql:
            return [{"id": NAME, "name": "NVDA swing"}]
        if "order_assignment_events" in sql:
            return [
                {
                    "perm_id": perm,
                    "assignment": (*tags, "event", datetime(2026, 10, 15, tzinfo=UTC)),
                }
                for (account, perm), tags in self._assignments.items()
                if account == params.get("account")
            ]
        if "broker.executions" in sql:
            return [f for f in self._fills if f["account_id"] == params["account"]]
        if "FROM ref.listings" in sql:
            return [{"security_id": SECURITY}]
        if "FROM ref.contracts" in sql:
            return []
        return []


@pytest.fixture
def ledger_api():
    accounts = [ACCOUNT, {**ACCOUNT, "account_id": "U2"}]
    fills = [
        fill("a", 1, 1, 10, at="2026-10-14T14:00:00"),
        # A corrected fill: same exec_id, newer version, different exec_time.
        fill("b", 2, 1, 10, at="2026-10-15T14:00:00"),
        fill("b", 2, 1, 11, at="2026-10-15T15:00:00", version=2),
        fill("c", 3, 1, 10, at="2026-10-16T14:00:00", account="U2", symbol="AAPL", listing_id=None),
        fill(
            "d",
            4,
            1,
            10,
            at="2026-10-16T15:00:00",
            account="U2",
            symbol="MSFT",
            listing_id="66666666-6666-6666-6666-666666666666",
            security_id="77777777-7777-7777-7777-777777777777",
        ),
    ]
    assignments = {("U1", 1): (THEME, STRATEGY, NAME)}
    repo = FakeRepo(accounts, fills, assignments)
    app.dependency_overrides[repository] = lambda: repo
    try:
        yield TestClient(app), repo
    finally:
        app.dependency_overrides.pop(repository)


def test_ledger_filters_the_whole_history_before_paging(ledger_api):
    client, _ = ledger_api
    body = client.get("/hub/api/v1/portfolio/ledger", params={"user_id": USER, "limit": 1}).json()
    assert body["total"] == 4 and len(body["items"]) == 1
    assert body["counts"] == {"tagged": 1, "untagged": 3, "not_taggable": 0}
    assert body["items"][0]["trading_symbol"] == "MSFT"  # newest first
    untagged = client.get(
        "/hub/api/v1/portfolio/ledger", params={"user_id": USER, "tag": "untagged"}
    ).json()
    assert untagged["total"] == 3 and untagged["counts"]["tagged"] == 1
    tagged = client.get(
        "/hub/api/v1/portfolio/ledger", params={"user_id": USER, "theme_id": THEME}
    ).json()
    assert [row["perm_id"] for row in tagged["items"]] == ["1"]
    assert tagged["items"][0]["theme_name"] == "AI"


def test_ledger_deduplicates_corrected_fills_and_matches_security_level_trades(ledger_api):
    client, _ = ledger_api
    body = client.get(
        "/hub/api/v1/portfolio/ledger", params={"user_id": USER, "listing_id": LISTING}
    ).json()
    rows = {row["perm_id"]: row for row in body["items"]}
    # The listing filter includes the security-level AAPL fill (listing_id unresolved).
    assert set(rows) == {"1", "2", "3"}
    corrected = rows["2"]
    assert corrected["fill_count"] == 1 and corrected["avg_price"] == "11"
    assert corrected["last_fill_at"].startswith("2026-10-15T15:00")
    assert len(body["accounts"]) == 2


def test_ledger_validates_inputs(ledger_api):
    client, repo = ledger_api
    url = "/hub/api/v1/portfolio/ledger"
    assert client.get(url, params={"user_id": USER, "broker_code": "ibkr"}).status_code == 422
    assert (
        client.get(url, params={"user_id": USER, "start": "2026-10-01T00:00:00"}).status_code == 422
    )
    assert client.get(url, params={"user_id": USER, "tag": "maybe"}).status_code == 422
    assert client.get(url).status_code == 422
    repo._accounts = [{**ACCOUNT, "account_id": f"U{n}"} for n in range(21)]
    assert client.get(url, params={"user_id": USER}).status_code == 422
