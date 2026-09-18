import gzip
import hashlib
import uuid
from datetime import UTC, date, datetime

import pandas as pd

from factorlab.storage.clickhouse import ClickHouseStorage, contract_id_for, instrument_id_for


class FakeClient:
    def __init__(self, query_rows=None):
        self.inserts = []
        self.query_rows = query_rows or []
        self.queries = []

    def insert(self, table, rows, column_names):
        self.inserts.append((table, rows, column_names))

    def query(self, query, parameters=None):
        self.queries.append((query, parameters))

        class Result:
            result_rows = self.query_rows

        return Result()


def test_stable_internal_ids_are_deterministic():
    assert instrument_id_for("NSE_EQ|INE002A01018") == instrument_id_for("NSE_EQ|INE002A01018")
    assert contract_id_for("NSE_FO|67003") == contract_id_for("NSE_FO|67003")


def test_archived_response_is_gzipped_and_hashed():
    client = FakeClient()
    storage = ClickHouseStorage(client)
    body = b'{"status":"ok"}'
    raw_id = storage.archive_http_response(
        source="upstox",
        source_url="https://example.test/data",
        response_body=body,
        status_code=200,
        fetched_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    table, rows, columns = client.inserts[0]
    assert table == "raw_http_archive"
    assert isinstance(raw_id, uuid.UUID)
    row = rows[0]
    assert gzip.decompress(row[columns.index("response_body")]) == body
    assert row[columns.index("response_sha256")] == hashlib.sha256(body).hexdigest()


def test_sync_and_candle_write_use_expected_clickhouse_rows():
    client = FakeClient()
    storage = ClickHouseStorage(client)
    instruments = [{
        "instrument_key": "NSE_EQ|INE002A01018",
        "trading_symbol": "RELIANCE",
        "name": "Reliance Industries",
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "lot_size": 1,
    }]
    lookup = storage.sync_instruments(instruments)
    storage.write_candles_1min(
        pd.DataFrame([{
            "timestamp": "2026-07-16T04:00:00Z",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100.5,
            "volume": 1000,
            "oi": 0,
        }]),
        instrument_id=lookup["RELIANCE"],
        symbol="RELIANCE",
    )

    assert [insert[0] for insert in client.inserts] == ["ref_instruments", "market_candles_1min"]
    candle_columns = client.inserts[1][2]
    candle_row = client.inserts[1][1][0]
    assert candle_row[candle_columns.index("contract_id")] == uuid.UUID(int=0)


def test_instrument_sync_preserves_first_seen_and_deactivates_removed_master_keys():
    first_seen = date(2026, 1, 2)
    last_seen = date(2026, 9, 10)
    current_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    removed_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    existing_rows = [
        (current_id, "NSE_EQ|CURRENT", "CURRENT", "Current Ltd", None, "NSE", "NSE_EQ",
         "EQ", "equity", b"IN", "IND", b"INR", 1, None, None, "1", "active",
         first_seen, last_seen, "upstox", None),
        (removed_id, "NSE_EQ|REMOVED", "REMOVED", "Removed Ltd", None, "NSE", "NSE_EQ",
         "EQ", "equity", b"IN", "IND", b"INR", 1, None, None, "2", "active",
         first_seen, last_seen, "upstox", None),
    ]
    client = FakeClient(query_rows=existing_rows)
    storage = ClickHouseStorage(client)

    storage.sync_instruments([{
        "instrument_key": "NSE_EQ|CURRENT",
        "trading_symbol": "CURRENT",
        "name": "Current Ltd",
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "lot_size": 1,
    }])

    _, rows, columns = client.inserts[0]
    by_key = {row[columns.index("instrument_key")]: row for row in rows}
    assert by_key["NSE_EQ|CURRENT"][columns.index("first_seen")] == first_seen
    assert by_key["NSE_EQ|CURRENT"][columns.index("status")] == "active"
    assert by_key["NSE_EQ|REMOVED"][columns.index("status")] == "inactive"
    assert by_key["NSE_EQ|REMOVED"][columns.index("last_seen")] == last_seen
    assert by_key["NSE_EQ|REMOVED"][columns.index("country_code")] == "IN"
    assert by_key["NSE_EQ|REMOVED"][columns.index("currency_code")] == "INR"


def test_multi_instrument_candles_use_one_clickhouse_insert():
    client = FakeClient()
    storage = ClickHouseStorage(client)
    frame = pd.DataFrame([{
        "timestamp": "2026-09-10T04:00:00Z",
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100.5,
        "volume": 1000,
        "oi": 0,
    }])
    first_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    second_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    count = storage.write_candles_1min_batch([
        {"candles": frame, "instrument_id": first_id, "symbol": "ONE", "raw_id": None},
        {"candles": frame, "instrument_id": second_id, "symbol": "TWO", "raw_id": None},
    ])

    assert count == 2
    assert len(client.inserts) == 1
    table, rows, columns = client.inserts[0]
    assert table == "market_candles_1min"
    assert len(rows) == 2
    assert {row[columns.index("symbol")] for row in rows} == {"ONE", "TWO"}


def test_latest_candle_times_filters_requested_series_and_applies_cutoff():
    requested_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    other_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    no_contract = uuid.UUID(int=0)
    latest = datetime(2026, 8, 17, 9, 59, tzinfo=UTC)
    cutoff = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    client = FakeClient(query_rows=[
        (requested_id, no_contract, latest),
        (other_id, no_contract, latest),
    ])
    storage = ClickHouseStorage(client)

    result = storage.latest_candle_times(
        [{"instrument_id": requested_id, "symbol": "TCS"}],
        before=cutoff,
    )

    assert result == {(requested_id, no_contract): latest}
    query, parameters = client.queries[0]
    assert "bar_time < {before:DateTime64(3, 'UTC')}" in query
    assert "instrument_id IN {instrument_ids:Array(UUID)}" in query
    assert parameters == {
        "source": "upstox",
        "instrument_ids": [requested_id],
        "before": cutoff,
    }


def test_expected_series_sync_activates_current_and_deactivates_removed_series():
    removed_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    client = FakeClient(query_rows=[(removed_id, uuid.UUID(int=0), "OLD", "demo", "1min")])
    storage = ClickHouseStorage(client)

    count = storage.sync_expected_india_series([
        {"instrument_id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "symbol": "TCS"}
    ], universe="demo")

    table, rows, columns = client.inserts[0]
    assert table == "india_expected_series"
    assert count == 1
    assert len(rows) == 2
    assert rows[0][columns.index("active")] is True
    assert rows[1][columns.index("active")] is False


def test_ingestion_run_records_running_and_terminal_versions():
    client = FakeClient()
    storage = ClickHouseStorage(client)

    run = storage.start_ingestion_run(
        pipeline="india_intraday_1min",
        source="upstox",
        universe="demo",
        requested_series=10,
    )
    storage.finish_ingestion_run(
        run,
        status="partial",
        successful_series=9,
        failed_series=1,
        rows_written=3375,
    )

    assert [insert[0] for insert in client.inserts] == ["ingestion_runs", "ingestion_runs"]
    columns = client.inserts[1][2]
    terminal = client.inserts[1][1][0]
    assert terminal[columns.index("run_id")] == run.run_id
    assert terminal[columns.index("status")] == "partial"
    assert terminal[columns.index("rows_written")] == 3375


def test_ingestion_run_accepts_non_market_observability_domain():
    client = FakeClient()
    storage = ClickHouseStorage(client)

    run = storage.start_ingestion_run(
        market_code="ALT_POLITICAL",
        pipeline="political_bootstrap",
        source="political",
    )

    columns = client.inserts[0][2]
    row = client.inserts[0][1][0]
    assert run.market_code == "ALT_POLITICAL"
    assert row[columns.index("market_code")] == "ALT_POLITICAL"
