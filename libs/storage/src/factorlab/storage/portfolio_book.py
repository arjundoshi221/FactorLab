"""Profile-owned portfolio catalogs and append-only durable-order assignments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from factorlab.clickhouse import rows
from factorlab.clickhouse.values import version

CatalogKind = Literal["themes", "strategies", "trade-names"]
CATALOGS: dict[str, tuple[str, str]] = {
    "themes": ("book.themes", "theme_id"),
    "strategies": ("book.strategies", "strategy_id"),
    "trade-names": ("book.trade_names", "trade_name_id"),
}


class PortfolioBookStorage:
    def __init__(self, client: Any) -> None:
        self.client = client

    def query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        return rows(
            self.client.query(
                sql,
                parameters=parameters,
                settings={
                    "readonly": 2,
                    "max_execution_time": 5,
                    "max_result_rows": 20000,
                    "result_overflow_mode": "throw",
                },
            )
        )

    def profile(self, user_id: UUID) -> None:
        if not self.query(
            "SELECT user_id FROM ref.app_users FINAL WHERE user_id = {user:UUID} AND active",
            {"user": user_id},
        ):
            raise ValueError("Active profile not found")

    def insert(self, table: str, record: dict[str, Any]) -> None:
        self.client.insert(table, [list(record.values())], column_names=list(record))

    def save_entry(
        self,
        kind: CatalogKind,
        user_id: UUID,
        *,
        name: str,
        description: str,
        active: bool,
        entry_id: UUID | None = None,
    ) -> dict[str, Any]:
        self.profile(user_id)
        table, key = CATALOGS[kind]
        now = datetime.now(UTC)
        previous: dict[str, Any] = {}
        if entry_id is not None:
            found = self.query(
                f"SELECT * FROM {table} FINAL WHERE {key} = {{id:UUID}} AND user_id = {{user:UUID}}",
                {"id": entry_id, "user": user_id},
            )
            if not found:
                raise LookupError("Catalog entry not found for profile")
            previous = found[0]
        entry_id = entry_id or uuid4()
        defaults: dict[str, Any]
        if kind == "themes":
            defaults = {
                "thesis": "",
                "inception_date": now.date(),
                "retired_date": None,
                "parent_theme_id": None,
                "tags": [],
                "author_entity_id": None,
                "owner_entity_id": None,
            }
        elif kind == "strategies":
            defaults = {
                "backtest_start_date": None,
                "paper_start_date": None,
                "live_start_date": None,
                "retired_date": None,
                "paper_account_ids": [],
                "live_account_ids": [],
                "config_hash": "0" * 64,
                "code_version": "",
                "universe_id": "",
                "author_entity_id": None,
            }
        else:
            defaults = {
                "theme_id": None,
                "strategy_id": None,
                "side": "long_short",
                "base_currency": "USD",
                "priority": "medium",
                "inception_date": now.date(),
                "target_hold_horizon_days": None,
                "exit_criteria": "",
                "opened_by_entity_id": None,
                "owner_entity_id": None,
                "closed_at": None,
                "close_reason": None,
            }
        record = (
            defaults
            | previous
            | {
                key: entry_id,
                "user_id": user_id,
                "name": name.strip(),
                "version": version(now),
                "ingested_at": now,
            }
        )
        if kind == "trade-names":
            record.update(thesis=description, status="active" if active else "closed")
        else:
            record.update(description=description, active=active)
        self.insert(table, record)
        return {
            "id": str(entry_id),
            "name": name.strip(),
            "description": description,
            "active": active,
            "user_id": str(user_id),
        }

    def assign(
        self,
        user_id: UUID,
        broker_code: str,
        account_id: str,
        perm_id: int,
        tags: tuple[UUID | None, UUID | None, UUID | None],
    ) -> dict[str, Any]:
        self.profile(user_id)
        if perm_id <= 0:
            raise ValueError("A durable positive perm_id is required")
        if any(tags) and not all(tags):
            raise ValueError("Select all three tags or clear all three")
        parameters = {
            "user": user_id,
            "broker": broker_code,
            "account": account_id,
            "perm": perm_id,
        }
        if not self.query(
            "SELECT account_id FROM ref.broker_account_owners FINAL WHERE user_id = {user:UUID} "
            "AND broker_code = {broker:String} AND account_id = {account:String}",
            parameters,
        ):
            raise LookupError("Account not found for profile")
        if not self.query(
            "SELECT perm_id FROM broker.executions FINAL WHERE broker_code = {broker:String} "
            "AND account_id = {account:String} AND perm_id = {perm:Int64} "
            "UNION ALL SELECT perm_id FROM broker.open_orders_snapshot FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND perm_id = {perm:Int64} LIMIT 1",
            parameters,
        ):
            raise LookupError("Order not found")
        for kind, tag_id in zip(CATALOGS, tags, strict=True):
            if tag_id is None:
                continue
            table, key = CATALOGS[kind]
            state = "status NOT IN ('closed', 'stopped_out')" if kind == "trade-names" else "active"
            if not self.query(
                f"SELECT {key} FROM {table} FINAL WHERE {key} = {{id:UUID}} "
                f"AND user_id = {{user:UUID}} AND {state}",
                {"id": tag_id, "user": user_id},
            ):
                raise ValueError("Tags must be active entries belonging to this profile")
        record = {
            "event_id": uuid4(),
            "user_id": user_id,
            "broker_code": broker_code,
            "account_id": account_id,
            "perm_id": perm_id,
            "theme_id": tags[0],
            "strategy_id": tags[1],
            "trade_name_id": tags[2],
            "changed_at": datetime.now(UTC),
        }
        self.insert("book.order_assignment_events", record)
        return record
