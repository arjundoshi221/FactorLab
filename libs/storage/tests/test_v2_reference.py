from datetime import UTC, date, datetime

import pytest

from factorlab.storage.canonical_ids import canonical_ids, contract_id
from factorlab.storage.v2_reference import UnresolvedReference, V2ReferenceWriter


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.inserts = []

    def query(self, sql, parameters):
        return Result(next(self.responses))

    def insert(self, table, rows, column_names):
        self.inserts.append((table, dict(zip(column_names, rows[0], strict=True))))


RECORD = {
    "instrument_key": "NSE_EQ|INE002A01018", "isin": "INE002A01018",
    "country_code": "IN", "exchange_code": "NSE", "currency_code": "INR",
    "trading_symbol": "RELIANCE", "name": "Reliance Industries",
    "security_type": "equity", "lot_size": 1,
}


def test_live_listing_uses_migration_identity_and_provider_alias():
    client = Client([[(b"XNSE", b"IN", b"INR")], [(b"INR",)], [], [], [], [(None,)]])
    ids = V2ReferenceWriter(client).upsert_listing(
        RECORD, alias_kind="upstox_instrument_key",
        alias_value=RECORD["instrument_key"], source="upstox",
        observed_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    assert ids == canonical_ids(RECORD)
    assert [table for table, _ in client.inserts] == [
        "ref.entities", "ref.securities", "ref.listings", "ref.identifier_aliases"
    ]
    assert client.inserts[2][1]["listing_id"] == ids[2]
    assert client.inserts[3][1]["target_id"] == ids[2]
    assert client.inserts[1][1]["security_type"] == "common"


def test_live_reference_keeps_migrated_first_dates_and_alias_start():
    first = date(1998, 1, 1)
    alias_start = date(2025, 1, 1)
    client = Client([
        [(b"XNSE", b"IN", b"INR")], [(b"INR",)],
        [(first, first, "Reliance Industries")],
        [(first, "INE002A01018", None, None, "", None, "")],
        [(first, None, True)], [(alias_start,)],
    ])
    V2ReferenceWriter(client).upsert_listing(
        RECORD, alias_kind="upstox_instrument_key",
        alias_value=RECORD["instrument_key"], source="upstox",
        observed_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    assert client.inserts[0][1]["first_seen"] == first
    assert client.inserts[1][1]["issue_date"] == first
    assert client.inserts[2][1]["first_traded"] == first
    assert client.inserts[3][1]["valid_from"] == alias_start


def test_unresolved_exchange_cannot_create_reference():
    client = Client([[]])
    with pytest.raises(UnresolvedReference, match="exchange/currency"):
        V2ReferenceWriter(client).upsert_listing(
            RECORD, alias_kind="upstox_instrument_key",
            alias_value=RECORD["instrument_key"], source="upstox",
        )
    assert client.inserts == []


def test_future_requires_existing_matching_underlying():
    listing = canonical_ids(RECORD)[2]
    future = {
        "contract_key": "NSE_FO|67003", "exchange_code": "NSE",
        "country_code": "IN", "expiry": date(2026, 9, 30), "lot_size": 250,
    }
    client = Client([[(b"NSE", b"IN")], [(None,)]])
    identifier = V2ReferenceWriter(client).upsert_future(
        future, underlying_listing_id=listing, source="upstox"
    )
    assert identifier == contract_id(future["contract_key"])
    assert client.inserts[0][0] == "ref.contracts"
    assert client.inserts[1][1]["target_id"] == identifier
