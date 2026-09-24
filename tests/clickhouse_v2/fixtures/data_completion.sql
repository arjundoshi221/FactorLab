-- Synthetic fixture for wave_04_backfill_data_completion.sql.
-- Apply after legacy 001/003/004 and v2 Wave 0/1/2 + data-completion schema.

INSERT INTO factorlab.ref_instruments
    (instrument_id, instrument_key, trading_symbol, exchange_code, market_code,
     country_code, currency_code, source, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000011'), 'IN-FUT',
     'NIFTY', 'NSE_FO', 'IND', 'IN', 'INR', 'upstox', 1, '2026-09-01 00:00:00.000'),
    (toUUID('00000000-0000-0000-0000-000000000012'), 'US-ST',
     'AAPL', 'NYSE', 'USA', 'US', 'USD', 'schwab', 1, '2026-09-01 00:00:00.000');

INSERT INTO factorlab.ref_contracts
    (contract_id, contract_key, instrument_id, trading_symbol, contract_type,
     segment, expiry, source, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000021'), 'IN-FUT-SEP',
     toUUID('00000000-0000-0000-0000-000000000011'), 'NIFTYSEP',
     'FUT', 'NSE_FO', '2026-09-24', 'upstox', 1, '2026-09-01 00:00:00.000');

INSERT INTO factorlab.ref_exchanges
    (exchange_code, country_code, market_code, currency_code, timezone,
     source, version, ingested_at)
VALUES
    ('NSE_FO', 'IN', 'IND', 'INR', 'Asia/Kolkata',
     'upstox', 1, '2026-09-01 00:00:00.000');

INSERT INTO ref.listings
    (listing_id, security_id, exchange_code, country_code, trading_symbol,
     mic, lot_size, is_primary, active, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000111'),
     toUUID('00000000-0000-0000-0000-000000000211'),
     'NSE_FO', 'IN', 'NIFTY', 'XNSE', 1, true, true, 1, '2026-09-01 00:00:00.000'),
    (toUUID('00000000-0000-0000-0000-000000000112'),
     toUUID('00000000-0000-0000-0000-000000000212'),
     'NYSE', 'US', 'AAPL', 'XNYS', 1, true, true, 1, '2026-09-01 00:00:00.000');

INSERT INTO ref.contracts
    (contract_id, underlying_listing_id, exchange_code, country_code,
     contract_type, expiry, right, exercise_style, multiplier, lot_size,
     weekly, active, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000121'),
     toUUID('00000000-0000-0000-0000-000000000111'),
     'NSE_FO', 'IN', 'future', '2026-09-24', '', '', 1, 1,
     false, true, 1, '2026-09-01 00:00:00.000');

INSERT INTO meta.migration_id_crosswalk
    (legacy_database, legacy_table, legacy_key, source_hash, target_kind,
     target_id, approved_by, approved_at, evidence, version, ingested_at)
SELECT
    'factorlab', 'ref_instruments', instrument_key,
    lower(hex(SHA256(toJSONString(tuple(*))))), 'listing',
    if(instrument_key = 'IN-FUT',
       toUUID('00000000-0000-0000-0000-000000000111'),
       toUUID('00000000-0000-0000-0000-000000000112')),
    'test', toDateTime64('2026-09-01 00:00:00', 3, 'UTC'), '{}',
    1, toDateTime64('2026-09-01 00:00:00', 3, 'UTC')
FROM factorlab.ref_instruments FINAL;

INSERT INTO meta.migration_id_crosswalk
    (legacy_database, legacy_table, legacy_key, source_hash, target_kind,
     target_id, approved_by, approved_at, evidence, version, ingested_at)
SELECT
    'factorlab', 'ref_contracts', contract_key,
    lower(hex(SHA256(toJSONString(tuple(*))))), 'contract',
    toUUID('00000000-0000-0000-0000-000000000121'),
    'test', toDateTime64('2026-09-01 00:00:00', 3, 'UTC'), '{}',
    1, toDateTime64('2026-09-01 00:00:00', 3, 'UTC')
FROM factorlab.ref_contracts FINAL;

INSERT INTO meta.migration_reference_enrichment
    (legacy_database, legacy_table, legacy_key, source_hash, exchange_code,
     mic, session_timezone, regular_open, regular_close, currency_code,
     approved_by, approved_at, evidence, version, ingested_at)
SELECT
    'factorlab', 'ref_exchanges', exchange_code,
    lower(hex(SHA256(toJSONString(tuple(*))))), exchange_code,
    'XNSE', 'Asia/Kolkata', '09:15', '15:30', 'INR',
    'test', toDateTime64('2026-09-01 00:00:00', 3, 'UTC'), '{}',
    1, toDateTime64('2026-09-01 00:00:00', 3, 'UTC')
FROM factorlab.ref_exchanges FINAL;

INSERT INTO factorlab.market_candles_1min
    (instrument_id, contract_id, symbol, market_code, bar_time,
     open, high, low, close, volume, oi, source, as_of_time, ingested_at, version)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000011'),
     toUUID('00000000-0000-0000-0000-000000000021'),
     'NIFTYSEP', 'IND', '2026-09-02 04:00:00.000',
     100, 102, 99, 101, 5, 50, 'upstox',
     '2026-09-02 04:01:00.000', '2026-09-02 04:02:00.000', 1);

INSERT INTO factorlab.india_expected_series
    (instrument_id, contract_id, symbol, source, universe, resolution,
     active, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000011'),
     toUUID('00000000-0000-0000-0000-000000000021'),
     'NIFTYSEP', 'upstox', 'nse_fo', '1min', true, 1, '2026-09-01 00:00:00.000'),
    (toUUID('00000000-0000-0000-0000-000000000011'),
     toUUID('00000000-0000-0000-0000-000000000000'),
     'NIFTY', 'upstox', 'nse_cash', '1min', true, 1, '2026-09-01 00:00:00.000');

INSERT INTO factorlab.us_expected_series
    (instrument_id, symbol, provider_symbol, source, resolution, universe,
     active, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000012'),
     'AAPL', 'AAPL.US', 'schwab', 'daily', 'sp500', true, 1,
     '2026-09-01 00:00:00.000');

INSERT INTO factorlab.us_session_coverage
    (instrument_id, symbol, source, resolution, trade_date,
     expected, actual, missing, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000012'),
     'AAPL', 'schwab', '1min', '2026-09-02',
     390, 389, 1, 1, '2026-09-02 21:00:00.000');

INSERT INTO factorlab.us_recovery_state
    (instrument_id, symbol, source, resolution, history_complete,
     last_bar, version, ingested_at)
VALUES
    (toUUID('00000000-0000-0000-0000-000000000012'),
     'AAPL', 'schwab', '1min', true,
     '2026-09-02 20:00:00.000', 1, '2026-09-02 21:00:00.000');

INSERT INTO factorlab.us_source_status
    (source, status, detail, checked_at, version)
VALUES ('schwab', 'ok', 'fixture', '2026-09-02 21:00:00.000', 1);
