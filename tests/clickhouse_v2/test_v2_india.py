import gzip
import hashlib
import uuid
from datetime import UTC, datetime

import pandas as pd

from factorlab.storage.v2_india import V2IndiaStorage


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self, listing, contract):
        self.listing = listing
        self.contract = contract
        self.inserts = []

    def insert(self, table, rows, column_names):
        self.inserts.append((table, [dict(zip(column_names, row, strict=True)) for row in rows]))

    def query(self, sql, parameters=None):
        if "FROM ref.listings AS l" in sql:
            return Result([(uuid.uuid4(), uuid.uuid4(), "common")])
        if "FROM ref.contracts FINAL" in sql:
            return Result([(self.contract,)])
        raise AssertionError(sql)


def test_raw_response_and_contract_bars_preserve_lineage_and_local_date():
    listing, contract, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    client = Client(listing, contract)
    storage = V2IndiaStorage(client)
    raw = b"\x00original\xffresponse"
    raw_id = storage.archive_http_response(
        source="upstox", source_url="https://example.invalid/market",
        response_body=raw, status_code=200,
    )
    archive = client.inserts[0][1][0]
    assert client.inserts[0][0] == "raw.archive"
    assert archive["raw_id"] == raw_id
    assert gzip.decompress(archive["response_body"]) == raw
    assert archive["response_sha256"] == hashlib.sha256(raw).hexdigest()

    storage._active_run_id = run_id
    frame = pd.DataFrame([{
        "timestamp": datetime(2026, 9, 24, 18, 45, tzinfo=UTC),
        "open": 100, "high": 101, "low": 99, "close": 100,
        "volume": 50, "oi": 10,
    }])
    assert storage.write_candles_1min(
        frame, instrument_id=listing, contract_id=contract,
        symbol="RELIANCE26SEPFUT", raw_id=raw_id,
    ) == 1
    table, records = client.inserts[-1]
    assert table == "market.futures_contract_bars"
    assert records[0]["contract_id"] == contract
    assert records[0]["underlying_listing_id"] == listing
    assert records[0]["trade_date"].isoformat() == "2026-09-25"
    assert records[0]["raw_id"] == raw_id
    assert records[0]["ingest_run_id"] == run_id
    assert not any(table == "market.bars" for table, _ in client.inserts)
