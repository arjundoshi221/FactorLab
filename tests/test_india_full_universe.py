import uuid

import pandas as pd
import scripts.factlab_india_clickhouse_5min as ingest

from factorlab.sources.upstox.candles import MarketQuoteOHLCBatch


def test_full_equity_series_uses_every_nse_eq_record_only_once():
    reliance_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    tcs_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    records = [
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE002A01018",
            "trading_symbol": "RELIANCE",
        },
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE002A01018",
            "trading_symbol": "RELIANCE",
        },
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE467B01029",
            "trading_symbol": "TCS",
        },
        {
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "instrument_key": "NSE_FO|12345",
            "trading_symbol": "RELIANCE26SEPFUT",
        },
    ]

    result = ingest.build_full_equity_series(
        records,
        {"RELIANCE": reliance_id, "TCS": tcs_id},
    )

    assert [(item.symbol, item.instrument_key) for item in result] == [
        ("RELIANCE", "NSE_EQ|INE002A01018"),
        ("TCS", "NSE_EQ|INE467B01029"),
    ]
    assert all(item.contract_id is None for item in result)


class FakeStorage:
    def __init__(self):
        self.writes = []

    def write_candles_1min_batch(self, batches):
        self.writes.append(batches)
        return sum(len(batch["candles"]) for batch in batches)


def _series(number: int) -> ingest.CandleSeries:
    return ingest.CandleSeries(
        f"NSE_EQ|KEY{number}",
        uuid.UUID(f"00000000-0000-0000-0000-{number:012d}"),
        f"SYMBOL{number}",
    )


def test_full_equity_poll_batches_writes_and_counts_missing(monkeypatch):
    series = [_series(1), _series(2), _series(3)]
    calls = []

    def fetch(session, keys, storage, *, limiter):
        del session, storage, limiter
        calls.append(keys)
        observed = frozenset(keys if len(keys) == 2 else [])
        candles = {
            key: pd.DataFrame([{
                "timestamp": pd.Timestamp("2026-09-10T04:00:00Z"),
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 10,
                "oi": 0,
            }])
            for key in observed
        }
        return MarketQuoteOHLCBatch(candles, observed, f"raw-{len(calls)}")

    monkeypatch.setattr(ingest, "fetch_market_quote_ohlc", fetch)
    storage = FakeStorage()

    successful, failed, rows = ingest.poll_full_equity_once(
        object(),
        series,
        storage,
        object(),
        batch_size=2,
    )

    assert calls == [
        ["NSE_EQ|KEY1", "NSE_EQ|KEY2"],
        ["NSE_EQ|KEY3"],
    ]
    assert (successful, failed, rows) == (2, 1, 2)
    written = [batch for insert in storage.writes for batch in insert]
    assert {batch["symbol"] for batch in written} == {"SYMBOL1", "SYMBOL2"}
    assert {batch["raw_id"] for batch in written} == {"raw-1"}
    assert len(storage.writes) == 1


def test_full_universe_sweep_aligns_five_seconds_after_next_minute():
    assert ingest.seconds_until_next_quote_sweep(now_epoch=20.0) == 45.0
    assert ingest.seconds_until_next_quote_sweep(now_epoch=59.5) == 5.5
