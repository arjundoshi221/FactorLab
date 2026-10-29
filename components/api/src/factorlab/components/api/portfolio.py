"""Read-only shared test profiles and broker-account portfolio views."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from factorlab.clickhouse import ClickHouse, rows

from .source_health import broker_snapshot_stale

router = APIRouter()


class ItemsPage(BaseModel):
    items: list[dict[str, Any]]


class ExecutionPage(ItemsPage):
    limit: int
    offset: int
    has_more: bool


class SnapshotPage(ItemsPage):
    snapshot_time: datetime | None
    age_seconds: float | None
    source_channel: str | None
    simulated: bool
    freshness: Literal["not_reported", "fresh", "stale"]


class AccountSummary(BaseModel):
    account: dict[str, Any]
    balances: SnapshotPage


class PortfolioRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return rows(
            self.client.query(
                sql,
                parameters=parameters or {},
                settings={
                    "readonly": 2,
                    "max_execution_time": 5,
                    "max_result_rows": 20000,
                    "result_overflow_mode": "throw",
                },
            )
        )

    def profiles(self) -> list[dict[str, Any]]:
        return self.query(
            "SELECT user_id, display_name, active FROM ref.app_users FINAL ORDER BY display_name, user_id"
        )

    def accounts(self, user_id: str = "") -> list[dict[str, Any]]:
        users = {str(user["user_id"]): user for user in self.profiles()}
        if user_id not in ("", "unassigned") and user_id not in users:
            raise HTTPException(404, "Profile not found")
        owners = {
            (owner["broker_code"], owner["account_id"]): owner
            for owner in self.query(
                "SELECT broker_code, account_id, user_id, simulated FROM ref.broker_account_owners FINAL"
            )
        }
        registered = {
            (account["broker_code"], account["account_id"]): account
            for account in self.query(
                "SELECT broker_code, account_id, account_mode, base_currency, booking_country, active FROM ref.broker_accounts FINAL"
            )
        }
        discovered = self.query(
            "SELECT broker_code, account_id, argMax(account_mode, observed_at) AS account_mode FROM ("
            "SELECT broker_code, account_id, account_mode, snapshot_time AS observed_at FROM broker.account_state_snapshot "
            "UNION ALL SELECT broker_code, account_id, account_mode, snapshot_time FROM broker.positions_snapshot "
            "UNION ALL SELECT broker_code, account_id, account_mode, snapshot_time FROM broker.open_orders_snapshot "
            "UNION ALL SELECT broker_code, account_id, account_mode, exec_time FROM broker.executions"
            ") GROUP BY broker_code, account_id"
        )
        for account in discovered:
            registered.setdefault((account["broker_code"], account["account_id"]), account)
        result = []
        for key, account in sorted(registered.items()):
            owner = owners.get(key)
            profile_id = str(owner["user_id"]) if owner else None
            if user_id == "unassigned" and profile_id is not None:
                continue
            if user_id not in ("", "unassigned") and profile_id != user_id:
                continue
            result.append(
                {
                    **account,
                    "user_id": profile_id,
                    "display_name": users.get(profile_id or "", {}).get(
                        "display_name", "Unassigned"
                    ),
                    "simulated": bool(owner["simulated"])
                    if owner
                    else str(key[1]).startswith("SIM_"),
                }
            )
        return result

    def account(self, broker_code: str, account_id: str, user_id: str = "") -> dict[str, Any]:
        for account in self.accounts(user_id):
            if (account["broker_code"], account["account_id"]) == (broker_code, account_id):
                return account
        raise HTTPException(404, "Account not found for selected profile")

    def snapshot(self, account: dict[str, Any], dataset: str) -> SnapshotPage:
        tables = {
            "positions": "broker.positions_snapshot",
            "account_state": "broker.account_state_snapshot",
            "open_orders": "broker.open_orders_snapshot",
        }
        table = tables[dataset]
        params = {
            "broker": account["broker_code"],
            "account": account["account_id"],
            "dataset": dataset,
        }
        manifests = self.query(
            "SELECT parseDateTime64BestEffort(JSONExtractString(snapshot, 'snapshot_time'), 3, 'UTC') AS snapshot_time, "
            "JSONExtractString(snapshot, 'source_channel') AS source_channel FROM meta.ingestion_runs FINAL "
            "ARRAY JOIN JSONExtractArrayRaw(metadata_json, 'broker_snapshots') AS snapshot "
            "WHERE pipeline IN ('ibkr_broker_snapshot', 'ibkr_broker_simulation', 'broker.flex_activity:ibkr') "
            "AND status != 'running' "
            "AND JSONExtractString(snapshot, 'broker_code') = {broker:String} "
            "AND JSONExtractString(snapshot, 'account_id') = {account:String} "
            "AND JSONExtractString(snapshot, 'dataset') = {dataset:String} "
            "ORDER BY snapshot_time DESC, started_at DESC LIMIT 1",
            params,
        )
        if not manifests:
            manifests = self.query(
                f"SELECT snapshot_time, source_channel FROM {table} FINAL "
                "WHERE broker_code = {broker:String} AND account_id = {account:String} "
                "ORDER BY snapshot_time DESC LIMIT 1",
                params,
            )
        if not manifests:
            return SnapshotPage(
                items=[],
                snapshot_time=None,
                age_seconds=None,
                source_channel=None,
                simulated=bool(account["simulated"]),
                freshness="not_reported",
            )
        latest = manifests[0]
        stamp = latest["snapshot_time"]
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        items = self.query(
            f"SELECT * FROM {table} FINAL WHERE broker_code = {{broker:String}} AND account_id = {{account:String}} "
            "AND snapshot_time = {at:DateTime64(3, 'UTC')} AND source_channel = {channel:String}",
            {**params, "at": stamp, "channel": latest["source_channel"]},
        )
        now = datetime.now(UTC)
        age = max(0, (now - stamp).total_seconds())
        return SnapshotPage(
            items=items,
            snapshot_time=stamp,
            age_seconds=age,
            source_channel=latest["source_channel"],
            simulated=bool(account["simulated"]),
            freshness="stale" if broker_snapshot_stale(stamp, now) else "fresh",
        )

    def executions(self, account: dict[str, Any], *, limit: int, offset: int) -> ExecutionPage:
        items = self.query(
            "SELECT * FROM broker.executions FINAL WHERE broker_code = {broker:String} "
            "AND account_id = {account:String} ORDER BY exec_time DESC, exec_id DESC "
            "LIMIT {limit:UInt32} OFFSET {offset:UInt32}",
            {
                "broker": account["broker_code"],
                "account": account["account_id"],
                "limit": limit + 1,
                "offset": offset,
            },
        )
        return ExecutionPage(
            items=items[:limit], limit=limit, offset=offset, has_more=len(items) > limit
        )


def repository() -> Iterator[PortfolioRepository]:
    db = None
    try:
        db = ClickHouse.from_environment()
        yield PortfolioRepository(db.client)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            503,
            "Portfolio data is unavailable. Check database access and required portfolio migrations.",
        ) from exc
    finally:
        if db is not None:
            db.close()


Repo = Annotated[PortfolioRepository, Depends(repository)]
ProfileFilter = Annotated[str, Query(max_length=36)]


def validate_profile_filter(value: str) -> str:
    if value in ("", "unassigned"):
        return value
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(422, "user_id must be a UUID or unassigned") from exc


@router.get("/profiles", response_model=ItemsPage)
def profiles(repo: Repo) -> ItemsPage:
    return ItemsPage(items=repo.profiles())


@router.get("/accounts", response_model=ItemsPage)
def accounts(repo: Repo, user_id: ProfileFilter = "") -> ItemsPage:
    return ItemsPage(items=repo.accounts(validate_profile_filter(user_id)))


@router.get("/accounts/{broker_code}/{account_id}/summary", response_model=AccountSummary)
def summary(
    repo: Repo, broker_code: str, account_id: str, user_id: ProfileFilter = ""
) -> AccountSummary:
    account = repo.account(broker_code, account_id, validate_profile_filter(user_id))
    return AccountSummary(account=account, balances=repo.snapshot(account, "account_state"))


@router.get("/accounts/{broker_code}/{account_id}/positions", response_model=SnapshotPage)
def positions(
    repo: Repo, broker_code: str, account_id: str, user_id: ProfileFilter = ""
) -> SnapshotPage:
    return repo.snapshot(
        repo.account(broker_code, account_id, validate_profile_filter(user_id)), "positions"
    )


@router.get("/accounts/{broker_code}/{account_id}/open-orders", response_model=SnapshotPage)
def open_orders(
    repo: Repo, broker_code: str, account_id: str, user_id: ProfileFilter = ""
) -> SnapshotPage:
    return repo.snapshot(
        repo.account(broker_code, account_id, validate_profile_filter(user_id)), "open_orders"
    )


@router.get("/accounts/{broker_code}/{account_id}/executions", response_model=ExecutionPage)
def executions(
    repo: Repo,
    broker_code: str,
    account_id: str,
    user_id: ProfileFilter = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> ExecutionPage:
    return repo.executions(
        repo.account(broker_code, account_id, validate_profile_filter(user_id)),
        limit=limit,
        offset=offset,
    )
