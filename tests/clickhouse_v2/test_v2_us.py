import uuid
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pandas as pd

from factorlab.storage.v2_us import V2USStorage


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self):
        self.inserts = []

    def query(self, sql, parameters=None):
        if "FROM ref.exchanges FINAL" in sql:
            return Result([(0,)])
        if "FROM ref.listings AS l" in sql:
            return Result([(uuid.uuid4(), uuid.uuid4(), "common")])
        raise AssertionError(sql)

    def insert(self, table, records, column_names):
        self.inserts.append((table, [dict(zip(column_names, row, strict=True)) for row in records]))


def test_us_daily_bar_uses_local_session_date_and_run_lineage():
    client = Client()
    storage = V2USStorage(client)
    listing_id, run_id, raw_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    storage._active_run_id = run_id
    frame = pd.DataFrame([{
        "trade_date": date(2026, 9, 23), "open": 10, "high": 11,
        "low": 9, "close": 10, "volume": 100,
    }])
    assert storage.write_daily(frame, instrument_id=listing_id,
                               symbol="AAPL", raw_id=raw_id) == 1
    table, records = client.inserts[0]
    assert table == "market.bars"
    assert records[0]["listing_id"] == listing_id
    assert records[0]["bar_time"] == datetime(2026, 9, 23, 4, tzinfo=UTC)
    assert records[0]["trade_date"] == date(2026, 9, 23)
    assert records[0]["raw_id"] == raw_id
    assert records[0]["ingest_run_id"] == run_id


def test_us_reference_maps_mic_and_seeds_verified_cboe_exchange():
    client = Client()
    storage = V2USStorage(client)
    storage.references.upsert_listing = Mock(return_value=(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()))
    storage._identity_status = Mock()

    storage.upsert_resolved_constituents([{
        "symbol": "CBOE", "exchange": "BATS", "currency": "USD",
        "name": "Cboe Global Markets",
    }])
    exchange = next(records[0] for table, records in client.inserts if table == "ref.exchanges")
    assert (exchange["exchange_code"], exchange["mic"]) == ("Cboe BZX", "BATS")
    assert storage.references.upsert_listing.call_args.args[0]["exchange_code"] == "Cboe BZX"

    storage.upsert_resolved_constituents([{
        "symbol": "AAPL", "exchange": "XNAS", "currency": "USD", "name": "Apple",
    }])
    assert storage.references.upsert_listing.call_args.args[0]["exchange_code"] == "NASDAQ"
