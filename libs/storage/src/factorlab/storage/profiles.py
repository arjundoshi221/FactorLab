"""Registration of shared test profiles, account metadata and stable ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from factorlab.clickhouse.values import version
from factorlab.ingest.profiles import ProfileRegistry


class ProfileStorage:
    def __init__(self, client: Any) -> None:
        self.client = client

    def sync(self, registry: ProfileRegistry, *, dry_run: bool = False) -> dict[str, int]:
        """Validate all existing links before writing; operator must serialize syncs."""
        result = self.client.query(
            "SELECT broker_code, account_id, user_id, simulated "
            "FROM ref.broker_account_owners FINAL"
        )
        owners = {
            (str(row[0]), str(row[1])): (UUID(str(row[2])), bool(row[3]))
            for row in result.result_rows
        }
        result = self.client.query(
            "SELECT broker_code, account_id, strategy_ids, closed_date, account_mode FROM ref.broker_accounts FINAL"
        )
        existing_accounts = {(str(row[0]), str(row[1])): row for row in result.result_rows}
        for account in registry.accounts:
            existing = owners.get((account.broker_code, account.account_id))
            if existing is not None and existing != (account.user_id, account.simulated):
                raise ValueError(
                    f"ownership or simulation status cannot change: {account.account_id}"
                )
            previous = existing_accounts.get((account.broker_code, account.account_id))
            if previous is not None and previous[4] != account.account_mode:
                raise ValueError(f"account mode cannot change: {account.account_id}")
        counts = {"users": len(registry.users), "accounts": len(registry.accounts)}
        if dry_run:
            return counts
        now = datetime.now(UTC)
        audit = {"version": version(now), "ingested_at": now}
        self._insert("ref.app_users", [user.model_dump() | audit for user in registry.users])
        for account in registry.accounts:
            previous = existing_accounts.get((account.broker_code, account.account_id))
            record = account.model_dump(exclude={"user_id", "simulated"})
            record.update(
                closed_date=previous[3] if previous else None,
                strategy_ids=previous[2] if previous else [],
            )
            self._insert("ref.broker_accounts", [record | audit])
        self._insert(
            "ref.broker_account_owners",
            [
                {
                    "broker_code": account.broker_code,
                    "account_id": account.account_id,
                    "user_id": account.user_id,
                    "simulated": account.simulated,
                }
                | audit
                for account in registry.accounts
            ],
        )
        return counts

    def _insert(self, table: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        columns = list(records[0])
        self.client.insert(
            table,
            [[record[column] for column in columns] for record in records],
            column_names=columns,
        )
