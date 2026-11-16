"""Independent profile catalogs, order tagging and recorded-history PnL routes."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from factorlab.components.api.portfolio import ItemsPage, PortfolioRepository, Repo
from factorlab.components.api.portfolio_attributes import Reader, enrich, parse_dims
from factorlab.components.api.portfolio_pnl import (
    FX_CONVERSIONS,
    TAG_FIELDS,
    identity,
    latest_fills,
    ledger,
    number,
    summarize,
)
from factorlab.components.api.portfolio_reconcile import (
    reconcile,
    statement,
    statement_meta,
    with_broker_pnl,
)
from factorlab.storage.portfolio_book import CATALOGS, CatalogKind, PortfolioBookStorage

router = APIRouter()

# IBKR statements, and so their year-to-date figures, use the New York trading day.
PERIOD_TIMEZONE = ZoneInfo("America/New_York")
# Flex cash transactions that are costs of the account rather than of one fill.
ACCOUNT_FEE_TYPES = {"fee": "fees", "commission": "commission_adjustments"}
ACCOUNT_FEE_FIELDS = ("fees", "commission_adjustments", "fx_conversions")


def period_start(since: date | None) -> datetime | None:
    if since is None:
        return None
    return datetime.combine(since, time(), PERIOD_TIMEZONE).astimezone(UTC)


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    active: bool = True

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme_id: UUID | None = None
    strategy_id: UUID | None = None
    trade_name_id: UUID | None = None

    @model_validator(mode="after")
    def complete(self) -> Assignment:
        values = (self.theme_id, self.strategy_id, self.trade_name_id)
        if any(values) and not all(values):
            raise ValueError("Select all three tags or clear all three")
        return self


def same_origin(request: Request) -> None:
    """Cloudflare Access authenticates the shared workspace; reject cross-site writes."""
    from urllib.parse import urlsplit

    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and urlsplit(origin).netloc != request.headers.get("host")
    ):
        raise HTTPException(403, "Cross-site portfolio writes are not allowed")


class Book:
    def __init__(self, repo: PortfolioRepository) -> None:
        self.repo = repo

    def profile(self, user_id: UUID) -> str:
        value = str(user_id)
        if not any(str(row["user_id"]) == value for row in self.repo.profiles()):
            raise HTTPException(404, "Profile not found")
        return value

    def catalog(self, kind: CatalogKind, user_id: UUID) -> list[dict[str, Any]]:
        self.profile(user_id)
        table, key = CATALOGS[kind]
        description = "thesis" if kind == "trade-names" else "description"
        active = "status NOT IN ('closed', 'stopped_out')" if kind == "trade-names" else "active"
        return self.repo.query(
            f"SELECT toString({key}) AS id, user_id, name, "
            f"{description} AS description, {active} AS active FROM {table} FINAL "
            "WHERE user_id = {user:UUID} ORDER BY name, id",
            {"user": user_id},
        )

    def assignments(self, account: dict[str, Any]) -> dict[int, dict[str, Any]]:
        records = (
            self.repo.query(
                "SELECT perm_id, argMax(tuple(theme_id, strategy_id, trade_name_id, event_id, changed_at), "
                "tuple(changed_at, event_id)) AS assignment FROM book.order_assignment_events "
                "WHERE broker_code = {broker:String} AND account_id = {account:String} "
                "AND user_id = {user:UUID} GROUP BY perm_id",
                {
                    "broker": account["broker_code"],
                    "account": account["account_id"],
                    "user": account["user_id"],
                },
            )
            if account.get("user_id")
            else []
        )
        return {
            int(row["perm_id"]): dict(
                zip((*TAG_FIELDS, "event_id", "changed_at"), row["assignment"], strict=True)
            )
            for row in records
        }

    def fills(
        self, account: dict[str, Any], assignments: dict[int, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        records = self.repo.query(
            "SELECT * FROM broker.executions FINAL WHERE broker_code = {broker:String} "
            "AND account_id = {account:String} ORDER BY exec_time, exec_id",
            {"broker": account["broker_code"], "account": account["account_id"]},
        )
        return latest_fills(
            [
                {
                    **row,
                    **assignments.get(int(row["perm_id"]), {}),
                    "simulated": account["simulated"],
                }
                for row in records
            ]
        )

    def orders(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        assignments = self.assignments(account)
        fills = self.fills(account, assignments)
        working = self.repo.snapshot(account, "open_orders")
        orders: dict[str, dict[str, Any]] = {}
        # Missing perm_ids get an execution-specific key; never group unrelated zero IDs.
        for fill in fills:
            perm = int(fill["perm_id"])
            key = str(perm) if perm > 0 else f"fill:{fill['exec_id']}"
            item = orders.setdefault(
                key,
                {
                    "key": key,
                    "perm_id": str(perm),
                    "trading_symbol": fill["trading_symbol"],
                    "currency": fill["currency"],
                    "side": fill["side"],
                    "status": "Executed",
                    "filled_quantity": number(0),
                    "fills": [],
                    "taggable": perm > 0 and bool(account.get("user_id")),
                    **dict.fromkeys(TAG_FIELDS),
                    **assignments.get(perm, {}),
                },
            )
            item["filled_quantity"] += number(fill["quantity"]) or 0
            item["fills"].append({**fill, "perm_id": str(perm)})
        for index, row in enumerate(working.items):
            perm = int(row["perm_id"])
            key = str(perm) if perm > 0 else f"working:{index}"
            previous = orders.get(key, {})
            orders[key] = {
                **row,
                **previous,
                "key": key,
                "perm_id": str(perm),
                "status": row["status"],
                "remaining_quantity": row["remaining_quantity"],
                "taggable": perm > 0 and bool(account.get("user_id")),
                "fills": previous.get("fills", []),
                **dict.fromkeys(TAG_FIELDS),
                **assignments.get(perm, {}),
            }
        return [
            {**row, "filled_quantity": str(row.get("filled_quantity", 0))}
            for row in reversed(list(orders.values()))
        ]

    def report_accounts(self, user_id: UUID, broker: str, account_id: str) -> list[dict[str, Any]]:
        profile = self.profile(user_id)
        if broker or account_id:
            if not broker or not account_id:
                raise HTTPException(422, "broker_code and account_id must be supplied together")
            return [self.repo.account(broker, account_id, profile)]
        return self.repo.accounts(profile)

    def fx_rates(self, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Latest broker ``ExchangeRate`` per account and currency: 1 ``currency`` = ``rate`` ``base``.

        Lets the UI show one report in any currency. Rates older than 30 days are left out,
        so a currency without a recent rate stays unconvertible rather than guessed.
        """
        bases = {
            (str(a["broker_code"]), str(a["account_id"])): str(a.get("base_currency") or "")
            for a in accounts
        }
        bases = {key: base for key, base in bases.items() if base}
        if not bases:
            return []
        rows = self.repo.query(
            "SELECT broker_code, account_id, currency, argMax(value_num, snapshot_time) AS rate, "
            "max(snapshot_time) AS as_of FROM broker.account_state_snapshot FINAL "
            "WHERE (broker_code, account_id) IN {accounts:Array(Tuple(String, String))} "
            "AND metric = 'ExchangeRate' AND segment = '' "
            "AND snapshot_time >= now() - INTERVAL 30 DAY "
            "GROUP BY broker_code, account_id, currency",
            {"accounts": sorted(bases)},
        )
        result = [
            {"currency": base, "base": base, "rate": "1", "as_of": None}
            for base in sorted(set(bases.values()))
        ]
        for row in rows:
            base = bases.get((str(row["broker_code"]), str(row["account_id"])))
            currency = str(row["currency"] or "")
            if not base or not currency or currency == base or not row["rate"]:
                continue
            result.append(
                {
                    "currency": currency,
                    "base": base,
                    "rate": str(row["rate"]),
                    "as_of": row["as_of"],
                }
            )
        return result

    def account_fees(
        self, accounts: list[dict[str, Any]], since: datetime | None
    ) -> list[dict[str, Any]]:
        """Account fees, commission adjustments and FX conversion commissions, as positive costs."""
        result = []
        start = since or datetime(1970, 1, 1, tzinfo=UTC)
        for account in accounts:
            params = {
                "broker": account["broker_code"],
                "account": account["account_id"],
                "since": start,
            }
            conversions = self.repo.query(
                "SELECT commission_ccy AS currency, sum(commission) AS amount "
                "FROM broker.executions FINAL WHERE broker_code = {broker:String} "
                "AND account_id = {account:String} AND product_type IN {fx:Array(String)} "
                "AND exec_time >= {since:DateTime64(3, 'UTC')} AND commission IS NOT NULL "
                "GROUP BY commission_ccy ORDER BY commission_ccy",
                {**params, "fx": sorted(FX_CONVERSIONS)},
            )
            records = self.repo.query(
                "SELECT currency, cash_flow_type, sum(amount) AS amount, count() AS events "
                "FROM broker.cash_flows FINAL WHERE broker_code = {broker:String} "
                "AND account_id = {account:String} AND cash_flow_type IN ('fee', 'commission') "
                "AND event_time >= {since:DateTime64(3, 'UTC')} "
                "GROUP BY currency, cash_flow_type ORDER BY currency, cash_flow_type",
                params,
            )
            by_currency: dict[str, dict[str, Decimal]] = defaultdict(
                lambda: dict.fromkeys(ACCOUNT_FEE_FIELDS, Decimal(0))
            )
            for row in conversions:
                # Fill commissions are already positive costs.
                by_currency[str(row["currency"]).strip() or "UNKNOWN"]["fx_conversions"] += Decimal(
                    str(row["amount"])
                )
            for row in records:
                field = ACCOUNT_FEE_TYPES[str(row["cash_flow_type"])]
                # Cash flows are signed credits; a debit (negative) is a positive cost.
                by_currency[str(row["currency"]).strip() or "UNKNOWN"][field] -= Decimal(
                    str(row["amount"])
                )
            for currency, costs in sorted(by_currency.items()):
                result.append(
                    {
                        "broker_code": account["broker_code"],
                        "account_id": account["account_id"],
                        "account_mode": account.get("account_mode", ""),
                        "simulated": account["simulated"],
                        "currency": currency,
                        **{field: str(value) for field, value in costs.items()},
                        "total": str(sum(costs.values(), Decimal(0))),
                    }
                )
        return result

    def taxes(self, account: dict[str, Any]) -> dict[str, Decimal]:
        """Transaction taxes per execution from Flex trade rows, as positive costs.

        IBKR includes them (e.g. GST on commissions, UK stamp duty) in cost basis and
        realized PnL, but they are not part of the commission. Fills not yet in a Flex
        statement have none until the next sync.
        """
        rows = self.repo.query(
            "SELECT attributes['ibExecID'] AS exec_id, any(attributes['taxes']) AS taxes "
            "FROM broker.statement_rows FINAL WHERE broker_code = {broker:String} "
            "AND account_id = {account:String} AND section = 'Trades' "
            "AND level_of_detail = 'EXECUTION' AND attributes['taxes'] NOT IN ('', '0') "
            "GROUP BY exec_id",
            {"broker": account["broker_code"], "account": account["account_id"]},
        )
        result = {}
        for row in rows:
            value = number(row["taxes"])
            if value:
                result[str(row["exec_id"])] = -value  # Flex reports taxes as signed cash
        return result

    def statement(self, account: dict[str, Any]) -> dict[str, Any] | None:
        """The account's latest Flex statement: its open positions and their FIFO cost basis."""
        params = {"broker": account["broker_code"], "account": account["account_id"]}
        headers = self.repo.query(
            "SELECT generated_at, statement_to FROM broker.statement_rows FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND section = 'FlexStatement' ORDER BY generated_at DESC LIMIT 1",
            params,
        )
        if not headers:
            return None
        rows = self.repo.query(
            "SELECT level_of_detail, vendor_id, trading_symbol, currency, asset_category, "
            "attributes['position'] AS position, attributes['costBasisMoney'] AS cost_basis, "
            "attributes['originatingOrderID'] AS perm_id FROM broker.statement_rows FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND section = 'OpenPositions' AND element = 'OpenPosition' "
            "AND generated_at = {generated:DateTime64(3, 'UTC')}",
            {**params, "generated": headers[0]["generated_at"]},
        )
        return statement(headers[0], rows)

    def pnl(
        self,
        user_id: UUID,
        kind: CatalogKind,
        accounts: list[dict[str, Any]],
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Tagged groups with IBKR's figures beside ours, and the all-time reconciliation."""
        fills: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        times = {}
        order_tags: dict[tuple[str, str, int], tuple[str | None, ...]] = {}
        statements: dict[tuple[str, str], dict[str, Any] | None] = {}
        # Bound the total request as well as each ClickHouse query.
        if len(accounts) > 20:
            raise HTTPException(422, "Select one account to report more than 20 accounts")
        for account in accounts:
            broker, account_id = str(account["broker_code"]), str(account["account_id"])
            assignments = self.assignments(account)
            for perm, tags in assignments.items():
                order_tags[broker, account_id, perm] = tuple(
                    str(tags[field]) if tags.get(field) else None for field in TAG_FIELDS
                )
            taxes = self.taxes(account)
            fills.extend(
                {**row, "taxes": taxes.get(str(row["exec_id"]))}
                for row in self.fills(account, assignments)
            )
            if len(fills) > 20000:
                raise HTTPException(
                    422, "Recorded history exceeds the 20,000-fill report limit; select one account"
                )
            snapshot = self.repo.snapshot(account, "positions")
            snapshots.extend([{**row, "simulated": account["simulated"]} for row in snapshot.items])
            times[account["broker_code"], account["account_id"]] = snapshot.snapshot_time
            statements[broker, account_id] = self.statement(account)
        marks = {
            identity(row): row for row in snapshots if row.get("product_type") not in FX_CONVERSIONS
        }
        details = ledger(fills, snapshots, times, since=since)
        everything = details if since is None else ledger(fills, snapshots, times)
        details = with_broker_pnl(details, fills, statements, marks, order_tags, since=since)
        field = CATALOGS[kind][1]
        names = {row["id"]: row["name"] for row in self.catalog(kind, user_id)}
        return {
            "items": [
                {
                    **row,
                    "name": names.get(
                        row["id"], "Unassigned" if row["id"] is None else "Archived entry"
                    ),
                }
                for row in summarize(details, field, period=since is not None)
            ],
            "reconciliation": reconcile(everything, fills, statements, marks),
            "statements": statement_meta(statements),
        }


def with_attributes(
    repo: Reader, groups: list[dict[str, Any]], names: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Label each group's holdings in one pass; excluded rows share their detail's labels."""
    details = [row for group in groups for row in group["details"]]
    labelled, failed = enrich(repo, details, names)
    position = 0
    result = []
    for group in groups:
        rows = labelled[position : position + len(group["details"])]
        position += len(group["details"])
        found = {(row["trading_symbol"], row["account_id"]): row.get("attributes") for row in rows}
        excluded = [
            {**row, "attributes": found.get((row["trading_symbol"], row["account_id"])) or {}}
            for row in group.get("excluded", [])
        ]
        result.append({**group, "details": rows, "excluded": excluded})
    return result, failed


@router.get("/catalogs/{kind}", response_model=ItemsPage)
def catalog(kind: CatalogKind, user_id: UUID, repo: Repo) -> ItemsPage:
    return ItemsPage(items=Book(repo).catalog(kind, user_id))


@router.post("/catalogs/{kind}", status_code=201)
def create_entry(
    kind: CatalogKind, user_id: UUID, body: CatalogEntry, repo: Repo, request: Request
) -> dict[str, Any]:
    return save_entry(kind, user_id, body, repo, request)


@router.put("/catalogs/{kind}/{entry_id}")
def update_entry(
    kind: CatalogKind,
    entry_id: UUID,
    user_id: UUID,
    body: CatalogEntry,
    repo: Repo,
    request: Request,
) -> dict[str, Any]:
    return save_entry(kind, user_id, body, repo, request, entry_id)


def save_entry(
    kind: CatalogKind,
    user_id: UUID,
    body: CatalogEntry,
    repo: Repo,
    request: Request,
    entry_id: UUID | None = None,
) -> dict[str, Any]:
    same_origin(request)
    try:
        return PortfolioBookStorage(repo.client).save_entry(
            kind, user_id, entry_id=entry_id, **body.model_dump()
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/accounts/{broker_code}/{account_id}/orders/{perm_id}/assignment")
def assign(
    broker_code: str,
    account_id: str,
    perm_id: int,
    user_id: UUID,
    body: Assignment,
    repo: Repo,
    request: Request,
) -> dict[str, Any]:
    same_origin(request)
    try:
        result = PortfolioBookStorage(repo.client).assign(
            user_id,
            broker_code,
            account_id,
            perm_id,
            (body.theme_id, body.strategy_id, body.trade_name_id),
        )
        return {**result, "perm_id": str(perm_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/accounts/{broker_code}/{account_id}/orders")
def orders(
    broker_code: str,
    account_id: str,
    user_id: UUID,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=20000)] = 0,
) -> dict[str, Any]:
    items = Book(repo).orders(repo.account(broker_code, account_id, str(user_id)))
    return {
        "items": items[offset : offset + limit],
        "limit": limit,
        "offset": offset,
        "has_more": len(items) > offset + limit,
    }


@router.get("/pnl/{kind}")
def pnl(
    kind: CatalogKind,
    user_id: UUID,
    repo: Repo,
    broker_code: str = "",
    account_id: str = "",
    since: date | None = None,
    dims: Annotated[str, Query(max_length=200)] = "",
) -> dict[str, Any]:
    """``since`` (a New York trading date, e.g. 1 January for YTD) windows realized PnL and fees.

    ``dims=sector`` labels every holding (``details`` and ``excluded``) with
    ``attributes.sector`` so the UI can pivot groups by it.
    """
    book = Book(repo)
    accounts = book.report_accounts(user_id, broker_code, account_id)
    start = period_start(since)
    report = book.pnl(user_id, kind, accounts, start)
    names = parse_dims(dims)
    if names:
        report["items"], report["unavailable_dimensions"] = with_attributes(
            repo, report["items"], names
        )
    return {
        **report,
        "basis": "available_recorded_history",
        "supported": ["stocks", "ETFs"],
        "period": None
        if since is None
        else {"since": since.isoformat(), "start": start, "timezone": "America/New_York"},
        "account_fees": book.account_fees(accounts, start),
        "fx_rates": book.fx_rates(accounts),
    }


@router.get(
    "/accounts/{broker_code}/{account_id}/orders/{perm_id}/assignment-history",
    response_model=ItemsPage,
)
def history(
    broker_code: str,
    account_id: str,
    perm_id: int,
    user_id: UUID,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ItemsPage:
    repo.account(broker_code, account_id, str(user_id))
    return ItemsPage(
        items=repo.query(
            "SELECT event_id, theme_id, strategy_id, trade_name_id, changed_at "
            "FROM book.order_assignment_events WHERE broker_code = {broker:String} "
            "AND account_id = {account:String} AND perm_id = {perm:Int64} AND user_id = {user:UUID} "
            "ORDER BY changed_at DESC, event_id DESC LIMIT {limit:UInt32}",
            {
                "broker": broker_code,
                "account": account_id,
                "perm": perm_id,
                "user": user_id,
                "limit": limit,
            },
        )
    )
