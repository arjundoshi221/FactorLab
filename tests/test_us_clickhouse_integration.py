"""Run with RUN_US_CLICKHOUSE_TESTS=1 against the isolated local container on 18123."""
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import clickhouse_connect
import pytest

from factorlab.api.hub import TABLE_PROFILES, HubRepository
from factorlab.api.us import USRepository
from factorlab.sources.schwab.market import normalize
from factorlab.storage.us_clickhouse import USStorage

pytestmark = pytest.mark.skipif(os.getenv("RUN_US_CLICKHOUSE_TESTS") != "1", reason="Isolated ClickHouse test not enabled")


def test_real_clickhouse_retries_reference_state_coverage_and_api():
    client = clickhouse_connect.get_client(host="127.0.0.1", port=18123, username="us_test",
                                          password="us_test_local", database="factorlab_us_test")
    root = Path(__file__).parents[1]
    for path in sorted((root / "sql/clickhouse").glob("*.sql")):
        for statement in path.read_text().split(";"):
            statement = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--")).strip()
            if statement:
                client.command(statement)
    storage = USStorage(client)
    symbol = "TEST" + uuid4().hex[:6].upper()
    now = datetime.now(UTC)
    raw_id = storage.archive_http_response(source="schwab", source_url="https://example.test",
                                           response_body=b"{}", status_code=200)
    lookup = storage.upsert_resolved_constituents([{
        "symbol": symbol, "name": "Test equity", "exchange": "XNAS",
        "currency": "USD", "instrument_type": "EQUITY",
    }], raw_id=raw_id)
    ident = lookup[symbol]
    storage.sync_expected_series([{"instrument_id": ident, "symbol": symbol,
                                  "provider_symbol": symbol}],
                                 source="schwab", universe="test", resolution="daily")
    storage.sync_expected_series([{"instrument_id": ident, "symbol": symbol,
                                  "provider_symbol": symbol}],
                                 source="schwab", universe="test", resolution="1min")
    frame = normalize([{"datetime": 1788528600000, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 10}],
                      "1min", now=datetime(2026, 9, 4, 21, tzinfo=UTC))
    assert len(frame) == 1
    for _ in range(2):
        storage.write_candles_1min(frame, instrument_id=ident, symbol=symbol, source="schwab", market_code="USA", raw_id=raw_id)
    daily_stamp = int(datetime(2026, 9, 4, 16, tzinfo=UTC).timestamp() * 1000)
    daily = normalize([{"datetime": daily_stamp, "open": 10, "high": 12,
                        "low": 9, "close": 11, "volume": 10}],
                      "daily", now=datetime(2026, 9, 5, tzinfo=UTC))
    for _ in range(2):
        storage.write_daily(daily, instrument_id=ident, symbol=symbol, raw_id=raw_id,
                            source="schwab")
    storage.record_daily_snapshot_coverage(
        [{"instrument_id": ident, "symbol": symbol}],
        [{"instrument_id": ident}], date(2026, 9, 4), source="schwab")
    storage.coverage(ident, symbol, "1min", datetime(2026, 9, 4, 13, 30, tzinfo=UTC),
                     datetime(2026, 9, 4, 20, tzinfo=UTC), frame.timestamp.iloc[0].to_pydatetime())
    state = storage.state(ident, "1min")
    state.update(symbol=symbol, history_complete=True, available_from=frame.timestamp.iloc[0].to_pydatetime(),
                 last_bar=frame.timestamp.iloc[0].to_pydatetime(), checked_through=now, full_refreshed_at=now)
    storage.save_state(state)
    assert storage.state(ident, "1min")["history_complete"]
    storage.source_status("ready", "Integration test", source="schwab")
    storage.source_status("ready", "Integration test", source="universe")
    repo = USRepository(client)
    for resolution in ("1min", "daily"):
        page = repo.candles(resolution, symbol=symbol, date_from=date(2026, 9, 4), date_to=date(2026, 9, 4), cursor=None, limit=10)
        assert len(page.items) == 1  # Two physical writes, one logical candle.
        assert page.items[0]["market_code"] == "USA"
        if resolution == "daily":
            assert page.items[0]["adj_close"] is None
    instruments = repo.instruments(trading_date=date(2026, 9, 4), search=symbol)
    assert instruments.items[0]["series"][0]["actual"] == 1
    assert instruments.items[0]["series"][0]["missing"] == 389
    assert repo.dashboard(date(2026, 9, 4))["source"]["status"] == "ready"
    india = HubRepository(client, database="factorlab_us_test")._profile_aggregate(
        "market_candles_1min", TABLE_PROFILES["market_candles_1min"], date(2026, 9, 4), market_code="IND")
    assert india["today_rows"] == 0  # US writes must not inflate India coverage.
