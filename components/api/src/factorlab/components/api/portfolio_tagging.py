"""Independent profile catalogs, order tagging and recorded-history PnL routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from factorlab.components.api.portfolio import ItemsPage, PortfolioRepository, Repo
from factorlab.components.api.portfolio_pnl import (
    TAG_FIELDS,
    latest_fills,
    ledger,
    number,
    summarize,
)
from factorlab.storage.portfolio_book import CATALOGS, CatalogKind, PortfolioBookStorage

router = APIRouter()


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

    def pnl(
        self, user_id: UUID, kind: CatalogKind, broker: str, account_id: str
    ) -> list[dict[str, Any]]:
        profile = self.profile(user_id)
        accounts = self.repo.accounts(profile)
        if broker or account_id:
            if not broker or not account_id:
                raise HTTPException(422, "broker_code and account_id must be supplied together")
            accounts = [self.repo.account(broker, account_id, profile)]
        fills: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        times = {}
        # Bound the total request as well as each ClickHouse query.
        if len(accounts) > 20:
            raise HTTPException(422, "Select one account to report more than 20 accounts")
        for account in accounts:
            fills.extend(self.fills(account, self.assignments(account)))
            if len(fills) > 20000:
                raise HTTPException(
                    422, "Recorded history exceeds the 20,000-fill report limit; select one account"
                )
            snapshot = self.repo.snapshot(account, "positions")
            snapshots.extend([{**row, "simulated": account["simulated"]} for row in snapshot.items])
            times[account["broker_code"], account["account_id"]] = snapshot.snapshot_time
        details = ledger(fills, snapshots, times)
        field = CATALOGS[kind][1]
        names = {row["id"]: row["name"] for row in self.catalog(kind, user_id)}
        return [
            {
                **row,
                "name": names.get(
                    row["id"], "Unassigned" if row["id"] is None else "Archived entry"
                ),
            }
            for row in summarize(details, field)
        ]


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
    kind: CatalogKind, user_id: UUID, repo: Repo, broker_code: str = "", account_id: str = ""
) -> dict[str, Any]:
    return {
        "items": Book(repo).pnl(user_id, kind, broker_code, account_id),
        "basis": "available_recorded_history",
        "supported": ["stocks", "ETFs"],
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
