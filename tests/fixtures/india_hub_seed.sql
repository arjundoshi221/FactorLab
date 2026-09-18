INSERT INTO ref_instruments (
    instrument_id, instrument_key, trading_symbol, name, isin, exchange_code,
    segment, instrument_type, asset_class, country_code, market_code,
    currency_code, lot_size, tick_size, freeze_quantity, exchange_token,
    status, first_seen, last_seen, source, raw_id, version, ingested_at
) VALUES
(
    toUUID('11111111-1111-1111-1111-111111111111'), 'NSE_EQ|TCS', 'TCS',
    'Tata Consultancy Services', 'INE467B01029', 'NSE', 'NSE_EQ', 'EQ',
    'equity', 'IN', 'IND', 'INR', 1, toDecimal64(0.05, 6), NULL, 'TCS',
    'active', toDate('2026-08-01'), toDate('2026-08-27'), 'upstox', NULL, 1, now64(3)
),
(
    toUUID('22222222-2222-2222-2222-222222222222'), 'NSE_EQ|INFY', 'INFY',
    'Infosys', 'INE009A01021', 'NSE', 'NSE_EQ', 'EQ', 'equity', 'IN', 'IND',
    'INR', 1, toDecimal64(0.05, 6), NULL, 'INFY', 'active',
    toDate('2026-08-01'), toDate('2026-08-27'), 'upstox', NULL, 1, now64(3)
),
(
    toUUID('33333333-3333-3333-3333-333333333333'), 'NSE_EQ|AARTIDRUGS', 'AARTIDRUGS',
    'Aarti Drugs Limited', 'INE767A01016', 'NSE', 'NSE_EQ', 'EQ', 'equity', 'IN',
    'IND', 'INR', 1, toDecimal64(0.05, 6), NULL, 'AARTIDRUGS', 'active',
    toDate('2026-08-01'), toDate('2026-08-27'), 'upstox', NULL, 1, now64(3)
);

INSERT INTO india_expected_series (
    instrument_id, contract_id, symbol, source, universe, resolution,
    active, version, ingested_at
) VALUES
(
    toUUID('11111111-1111-1111-1111-111111111111'), toUUID('00000000-0000-0000-0000-000000000000'),
    'TCS', 'upstox', 'test', '1min', true, 1, now64(3)
),
(
    toUUID('22222222-2222-2222-2222-222222222222'), toUUID('00000000-0000-0000-0000-000000000000'),
    'INFY', 'upstox', 'test', '1min', true, 1, now64(3)
);

INSERT INTO market_candles_1min
SELECT
    toUUID('11111111-1111-1111-1111-111111111111'),
    toUUID('00000000-0000-0000-0000-000000000000'),
    'TCS', 'IND',
    toDateTime64('2026-08-27 03:45:00', 3, 'UTC') + toIntervalMinute(number),
    toDecimal64(100 + number * 0.001, 6),
    toDecimal64(101 + number * 0.001, 6),
    toDecimal64(99 + number * 0.001, 6),
    toDecimal64(100.5 + number * 0.001, 6),
    toUInt64(1000 + number), NULL, 'upstox', NULL, now64(3), now64(3), 1
FROM numbers(240);

INSERT INTO market_candles_1min
SELECT
    toUUID(if(number < 375,
        '11111111-1111-1111-1111-111111111111',
        '22222222-2222-2222-2222-222222222222')),
    toUUID('00000000-0000-0000-0000-000000000000'),
    if(number < 375, 'TCS', 'INFY'), 'IND',
    toDateTime64('2026-08-26 03:45:00', 3, 'UTC') + toIntervalMinute(number % 375),
    toDecimal64(100 + (number % 375) * 0.001, 6),
    toDecimal64(101 + (number % 375) * 0.001, 6),
    toDecimal64(99 + (number % 375) * 0.001, 6),
    toDecimal64(100.5 + (number % 375) * 0.001, 6),
    toUInt64(1000 + number), NULL, 'upstox', NULL, now64(3), now64(3), 1
FROM numbers(750);
