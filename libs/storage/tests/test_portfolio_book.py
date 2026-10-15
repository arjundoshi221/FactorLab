from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from factorlab.storage.portfolio_book import PortfolioBookStorage

USER = UUID("d677331a-a04f-454a-8a1e-02425be2c101")
TAG = UUID("d677331a-a04f-454a-8a1e-02425be2c111")


class Client:
    def __init__(self):
        self.writes = []
        self.owner = True
        self.active_tags = True
        self.known_order = True
        self.entry: dict[str, Any] | None = None

    def query(self, sql, parameters, **kwargs):
        if "ref.app_users" in sql:
            records = [{"user_id": USER}] if parameters["user"] == USER else []
        elif "ref.broker_account_owners" in sql:
            records = [{"account_id": "A"}] if self.owner else []
        elif "broker.executions" in sql:
            records = [{"perm_id": 1}] if self.known_order else []
        elif "SELECT *" in sql:
            records = [self.entry] if self.entry else []
        else:
            records = [{"id": TAG}] if self.active_tags else []
        columns = list(records[0]) if records else []
        return SimpleNamespace(
            column_names=columns,
            result_rows=[tuple(row[key] for key in columns) for row in records],
        )

    def insert(self, table, data, column_names):
        self.writes.append((table, dict(zip(column_names, data[0], strict=True))))


def test_independent_catalog_defaults_and_preserving_existing_strategy_metadata():
    client = Client()
    store = PortfolioBookStorage(client)
    store.save_entry("trade-names", USER, name="Named bet", description="Thesis", active=True)
    record = client.writes[-1][1]
    assert record["theme_id"] is None and record["strategy_id"] is None
    assert record["user_id"] == USER and record["thesis"] == "Thesis"
    client.entry = {
        "strategy_id": TAG,
        "user_id": USER,
        "name": "old",
        "description": "old",
        "active": True,
        "config_hash": "a" * 64,
        "paper_account_ids": ["A"],
        "version": 1,
        "ingested_at": datetime.now(UTC),
    }
    store.save_entry(
        "strategies", USER, name="Renamed", description="New", active=False, entry_id=TAG
    )
    record = client.writes[-1][1]
    assert record["config_hash"] == "a" * 64 and record["paper_account_ids"] == ["A"]
    assert record["name"] == "Renamed" and not record["active"] and record["version"] > 1


def test_unknown_catalog_update_cannot_write_a_new_entry():
    client = Client()
    with pytest.raises(LookupError):
        PortfolioBookStorage(client).save_entry(
            "themes", USER, name="x", description="", active=True, entry_id=TAG
        )
    assert client.writes == []


@pytest.mark.parametrize(
    ("attribute", "error"),
    [("owner", LookupError), ("active_tags", ValueError), ("known_order", LookupError)],
)
def test_all_assignment_checks_complete_before_any_write(attribute, error):
    client = Client()
    setattr(client, attribute, False)
    with pytest.raises(error):
        PortfolioBookStorage(client).assign(USER, "test", "A", 1, (TAG, TAG, TAG))
    assert not client.writes


def test_assign_clear_and_audit_events_are_append_only():
    client = Client()
    store = PortfolioBookStorage(client)
    first = store.assign(USER, "test", "A", 1, (TAG, TAG, TAG))
    cleared = store.assign(USER, "test", "A", 1, (None, None, None))
    assert len(client.writes) == 2 and first["event_id"] != cleared["event_id"]
    assert cleared["theme_id"] is None
    assert all(table == "book.order_assignment_events" for table, _ in client.writes)


@pytest.mark.parametrize(("perm", "tags"), [(0, (TAG, TAG, TAG)), (1, (TAG, None, None))])
def test_durable_identity_and_complete_triples_required(perm, tags):
    client = Client()
    with pytest.raises(ValueError):
        PortfolioBookStorage(client).assign(USER, "test", "A", perm, tags)
    assert not client.writes
