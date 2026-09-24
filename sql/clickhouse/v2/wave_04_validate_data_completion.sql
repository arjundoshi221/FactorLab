-- check: contract-keyed candles are not supported India futures
SELECT count()
FROM {{source_database}}.market_candles_1min AS b FINAL
LEFT JOIN {{source_database}}.ref_contracts AS c FINAL ON c.contract_id = b.contract_id
WHERE b.contract_id != toUUID('00000000-0000-0000-0000-000000000000')
  AND (c.contract_id = toUUID('00000000-0000-0000-0000-000000000000')
       OR c.contract_type != 'FUT' OR b.market_code != 'IND');

-- check: remaining-data source rows lack legacy instrument references
SELECT count()
FROM (
    SELECT DISTINCT instrument_id FROM {{source_database}}.market_candles_1min FINAL
    WHERE contract_id != toUUID('00000000-0000-0000-0000-000000000000')
    UNION DISTINCT
    SELECT DISTINCT instrument_id FROM {{source_database}}.india_expected_series FINAL
    UNION DISTINCT
    SELECT DISTINCT instrument_id FROM {{source_database}}.us_expected_series FINAL
    UNION DISTINCT
    SELECT DISTINCT instrument_id FROM {{source_database}}.us_session_coverage FINAL
    UNION DISTINCT
    SELECT DISTINCT instrument_id FROM {{source_database}}.us_recovery_state FINAL
) AS s
LEFT JOIN {{source_database}}.ref_instruments AS i FINAL
    ON i.instrument_id = s.instrument_id
WHERE i.instrument_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: expected futures lack legacy contract references
SELECT count()
FROM {{source_database}}.india_expected_series AS e FINAL
LEFT JOIN {{source_database}}.ref_contracts AS c FINAL ON c.contract_id = e.contract_id
WHERE e.contract_id != toUUID('00000000-0000-0000-0000-000000000000')
  AND c.contract_id = toUUID('00000000-0000-0000-0000-000000000000');
