"""raw.archive writes: generic archive_raw and the unchanged HTTP wrapper."""

import gzip
import hashlib
import json
from datetime import UTC, datetime

from factorlab.shared.ingest.provider import RawCapture
from factorlab.storage.v2_us import V2USStorage


class Client:
    def __init__(self):
        self.inserts = []

    def insert(self, table, records, column_names):
        self.inserts.append((table, [dict(zip(column_names, row, strict=True)) for row in records]))


FETCHED = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)


def test_archive_raw_records_transport_channel_and_body():
    client = Client()
    capture = RawCapture(body=b'{"a":1}', request_key="portfolio:paper", transport="tcp_socket",
                         fetched_at=FETCHED, source_url="ibkr-gateway://paper/portfolio",
                         metadata={"records": 1})
    raw_id = V2USStorage(client).archive_raw(capture, source="ibkr", source_channel="paper_gateway")

    table, [row] = client.inserts[0]
    assert table == "raw.archive"
    assert row["raw_id"] == raw_id
    assert (row["source"], row["source_channel"], row["transport"]) == (
        "ibkr", "paper_gateway", "tcp_socket")
    assert row["country_code"] == "US"
    assert row["request_key"] == "portfolio:paper"
    assert row["status_code"] is None
    assert gzip.decompress(row["response_body"]) == b'{"a":1}'
    assert row["response_sha256"] == hashlib.sha256(b'{"a":1}').hexdigest()
    assert row["fetched_at"] == row["as_of_time"] == FETCHED
    assert json.loads(row["metadata_json"]) == {"records": 1}


def test_archive_http_response_keeps_its_row_shape():
    client = Client()
    V2USStorage(client).archive_http_response(
        source="schwab", source_url="https://api/x", response_body=b"body", status_code=200,
        response_headers={"b": "2", "a": "1"}, fetch_key="AAPL", content_type="application/json",
        metadata={"m": 1}, fetched_at=FETCHED,
    )
    _, [row] = client.inserts[0]
    assert list(row) == [
        "raw_id", "source", "source_channel", "transport", "country_code", "source_url",
        "request_key", "status_code", "response_headers", "response_body", "content_type",
        "content_encoding", "response_sha256", "fetched_at", "window_start_at", "event_count",
        "as_of_time", "metadata_json",
    ]
    assert (row["source"], row["source_channel"], row["transport"]) == ("schwab", "schwab", "http")
    assert row["response_headers"] == '{"a": "1", "b": "2"}'
    assert row["status_code"] == 200
    assert row["content_type"] == "application/json"
    assert row["request_key"] == "AAPL"
