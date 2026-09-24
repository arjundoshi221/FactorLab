-- check: duplicate minute spot-bar target keys
SELECT count()
FROM (
    SELECT instrument_id, source, bar_time
    FROM {{source_database}}.market_candles_1min FINAL
    WHERE contract_id = toUUID('00000000-0000-0000-0000-000000000000')
    GROUP BY instrument_id, source, bar_time
    HAVING count() > 1
);

-- check: duplicate daily spot-bar target keys
SELECT count()
FROM (
    SELECT instrument_id, source, trade_date
    FROM {{source_database}}.market_candles_daily FINAL
    WHERE contract_id = toUUID('00000000-0000-0000-0000-000000000000')
    GROUP BY instrument_id, source, trade_date
    HAVING count() > 1
);

-- check: minute spot bars with missing instruments
SELECT count()
FROM {{source_database}}.market_candles_1min AS c FINAL
LEFT JOIN {{source_database}}.ref_instruments AS i FINAL ON i.instrument_id = c.instrument_id
WHERE c.contract_id = toUUID('00000000-0000-0000-0000-000000000000')
  AND i.instrument_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: daily spot bars with missing instruments
SELECT count()
FROM {{source_database}}.market_candles_daily AS c FINAL
LEFT JOIN {{source_database}}.ref_instruments AS i FINAL ON i.instrument_id = c.instrument_id
WHERE c.contract_id = toUUID('00000000-0000-0000-0000-000000000000')
  AND i.instrument_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: contract-keyed minute bars with missing legacy contract rows
SELECT count()
FROM (
    SELECT DISTINCT contract_id
    FROM {{source_database}}.market_candles_1min FINAL
    WHERE contract_id != toUUID('00000000-0000-0000-0000-000000000000')
) AS c
LEFT JOIN {{source_database}}.ref_contracts AS r FINAL ON r.contract_id = c.contract_id
WHERE r.contract_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: contract-keyed daily bars with missing legacy contract rows
SELECT count()
FROM (
    SELECT DISTINCT contract_id
    FROM {{source_database}}.market_candles_daily FINAL
    WHERE contract_id != toUUID('00000000-0000-0000-0000-000000000000')
) AS c
LEFT JOIN {{source_database}}.ref_contracts AS r FINAL ON r.contract_id = c.contract_id
WHERE r.contract_id = toUUID('00000000-0000-0000-0000-000000000000');
