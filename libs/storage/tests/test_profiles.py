from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from factorlab.ingest.profiles import ProfileRegistry
from factorlab.storage.profiles import ProfileStorage

USER = UUID("d677331a-a04f-454a-8a1e-02425be2c101")
OTHER = UUID("d677331a-a04f-454a-8a1e-02425be2c102")


def registry():
    return ProfileRegistry.model_validate(
        {
            "users": [{"user_id": USER, "display_name": "Alice"}],
            "accounts": [
                {
                    "user_id": USER,
                    "broker_code": "ibkr",
                    "account_id": "SIM_A",
                    "account_mode": "paper",
                    "base_currency": "USD",
                    "booking_country": "US",
                    "opened_date": "2026-10-01",
                    "simulated": True,
                }
            ],
        }
    )


class Client:
    def __init__(self, owner=USER):
        self.owner = owner
        self.inserts = []

    def query(self, sql):
        if "broker_account_owners" in sql:
            return SimpleNamespace(result_rows=[("ibkr", "SIM_A", self.owner, True)])
        return SimpleNamespace(result_rows=[("ibkr", "SIM_A", [OTHER], date(2026, 10, 2), "paper")])

    def insert(self, table, records, column_names):
        self.inserts.append(
            (table, [dict(zip(column_names, record, strict=True)) for record in records])
        )


def test_sync_preserves_strategy_links_and_closed_date():
    client = Client()
    store = ProfileStorage(client)
    assert store.sync(registry()) == {"users": 1, "accounts": 1}
    account = next(
        records[0] for table, records in client.inserts if table == "ref.broker_accounts"
    )
    assert account["strategy_ids"] == [OTHER]
    assert account["closed_date"] == date(2026, 10, 2)
    client.inserts.clear()
    store.sync(registry(), dry_run=True)
    assert client.inserts == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_reassignment_is_rejected_before_any_write(dry_run):
    client = Client(owner=OTHER)
    with pytest.raises(ValueError, match="cannot change"):
        ProfileStorage(client).sync(registry(), dry_run=dry_run)
    assert not client.inserts
