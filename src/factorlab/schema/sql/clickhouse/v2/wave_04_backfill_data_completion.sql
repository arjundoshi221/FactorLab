-- Contract-keyed source bars are facts, not a continuous rolled series.
INSERT INTO market.futures_contract_bars
SELECT
    l.country_code, l.listing_id, v2c.contract_id,
    b.instrument_id, b.contract_id, b.symbol, '1min',
    if(formatDateTime(b.bar_time, '%H:%M', 'Asia/Kolkata') < r.regular_open, 'pre',
       if(formatDateTime(b.bar_time, '%H:%M', 'Asia/Kolkata') < r.regular_close,
          'regular', 'post')),
    b.bar_time, toDate(b.bar_time, 'Asia/Kolkata'),
    b.open, b.high, b.low, b.close, b.volume, b.oi,
    b.source, b.raw_id, toUUID('{{migration_run_id}}'),
    b.as_of_time, b.ingested_at, b.migration_source_hash, now64(3, 'UTC'),
    greatest(b.version, target.version + 1)
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.market_candles_1min FINAL
    WHERE contract_id != toUUID('00000000-0000-0000-0000-000000000000')
) AS b
INNER JOIN (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.ref_contracts FINAL
) AS c ON c.contract_id = b.contract_id
INNER JOIN (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.ref_instruments FINAL
) AS i ON i.instrument_id = b.instrument_id
INNER JOIN meta.migration_id_crosswalk AS cx FINAL
    ON cx.legacy_database = '{{source_database}}'
   AND cx.legacy_table = 'ref_contracts'
   AND cx.legacy_key = c.contract_key
   AND cx.source_hash = c.migration_source_hash
   AND cx.target_kind = 'contract' AND cx.approved_at IS NOT NULL
INNER JOIN meta.migration_id_crosswalk AS ix FINAL
    ON ix.legacy_database = '{{source_database}}'
   AND ix.legacy_table = 'ref_instruments'
   AND ix.legacy_key = i.instrument_key
   AND ix.source_hash = i.migration_source_hash
   AND ix.target_kind = 'listing' AND ix.approved_at IS NOT NULL
INNER JOIN ref.contracts AS v2c FINAL ON v2c.contract_id = cx.target_id
INNER JOIN ref.listings AS l FINAL ON l.listing_id = ix.target_id
INNER JOIN meta.migration_reference_enrichment AS r FINAL
    ON r.legacy_database = '{{source_database}}'
   AND r.legacy_table = 'ref_exchanges'
   AND r.legacy_key = l.exchange_code AND r.approved_at IS NOT NULL
LEFT JOIN market.futures_contract_bars AS target FINAL
    ON target.contract_id = v2c.contract_id
   AND target.resolution = '1min'
   AND target.bar_time = b.bar_time AND target.source = b.source
WHERE c.contract_type = 'FUT' AND v2c.contract_type = 'future'
  AND b.market_code = 'IND' AND l.country_code = 'IN'
  AND v2c.underlying_listing_id = l.listing_id
  AND target.source_hash != b.migration_source_hash;

INSERT INTO meta.expected_series
SELECT
    'IN', l.listing_id,
    if(e.contract_id = toUUID('00000000-0000-0000-0000-000000000000'),
       NULL, toNullable(cx.target_id)),
    e.instrument_id, e.contract_id, 'india_expected_series',
    e.symbol, NULL, e.source, e.universe, e.resolution, e.active,
    e.migration_source_hash, greatest(e.version, target.version + 1),
    e.ingested_at, now64(3, 'UTC')
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.india_expected_series FINAL
) AS e
INNER JOIN (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.ref_instruments FINAL
) AS i ON i.instrument_id = e.instrument_id
INNER JOIN meta.migration_id_crosswalk AS ix FINAL
    ON ix.legacy_database = '{{source_database}}'
   AND ix.legacy_table = 'ref_instruments'
   AND ix.legacy_key = i.instrument_key AND ix.source_hash = i.migration_source_hash
   AND ix.target_kind = 'listing' AND ix.approved_at IS NOT NULL
INNER JOIN ref.listings AS l FINAL ON l.listing_id = ix.target_id AND l.country_code = 'IN'
LEFT JOIN (
    SELECT c.contract_id AS legacy_contract_id, x.target_id AS target_id
    FROM (
        SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
        FROM {{source_database}}.ref_contracts FINAL
    ) AS c
    INNER JOIN meta.migration_id_crosswalk AS x FINAL
        ON x.legacy_database = '{{source_database}}'
       AND x.legacy_table = 'ref_contracts'
       AND x.legacy_key = c.contract_key AND x.source_hash = c.migration_source_hash
       AND x.target_kind = 'contract' AND x.approved_at IS NOT NULL
) AS cx ON cx.legacy_contract_id = e.contract_id
LEFT JOIN meta.expected_series AS target FINAL
    ON target.source_table = 'india_expected_series'
   AND target.source = e.source AND target.legacy_instrument_id = e.instrument_id
   AND target.legacy_contract_id = e.contract_id AND target.resolution = e.resolution
WHERE (e.contract_id = toUUID('00000000-0000-0000-0000-000000000000')
       OR cx.target_id != toUUID('00000000-0000-0000-0000-000000000000'))
  AND (target.source_hash != e.migration_source_hash
       OR target.listing_id != l.listing_id);

INSERT INTO meta.expected_series
SELECT
    'US', l.listing_id, NULL,
    e.instrument_id, toUUID('00000000-0000-0000-0000-000000000000'),
    'us_expected_series', e.symbol, toNullable(e.provider_symbol),
    e.source, e.universe, e.resolution, e.active,
    e.migration_source_hash, greatest(e.version, target.version + 1),
    e.ingested_at, now64(3, 'UTC')
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.us_expected_series FINAL
) AS e
INNER JOIN (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.ref_instruments FINAL
) AS i ON i.instrument_id = e.instrument_id
INNER JOIN meta.migration_id_crosswalk AS ix FINAL
    ON ix.legacy_database = '{{source_database}}'
   AND ix.legacy_table = 'ref_instruments'
   AND ix.legacy_key = i.instrument_key AND ix.source_hash = i.migration_source_hash
   AND ix.target_kind = 'listing' AND ix.approved_at IS NOT NULL
INNER JOIN ref.listings AS l FINAL ON l.listing_id = ix.target_id AND l.country_code = 'US'
LEFT JOIN meta.expected_series AS target FINAL
    ON target.source_table = 'us_expected_series'
   AND target.source = e.source AND target.legacy_instrument_id = e.instrument_id
   AND target.legacy_contract_id = toUUID('00000000-0000-0000-0000-000000000000')
   AND target.resolution = e.resolution
WHERE target.source_hash != e.migration_source_hash
   OR target.listing_id != l.listing_id;

INSERT INTO meta.session_coverage
SELECT
    'US', l.listing_id, c.instrument_id, c.symbol, c.source, c.resolution,
    c.trade_date, c.expected, c.actual, c.missing, c.migration_source_hash,
    greatest(c.version, target.version + 1), c.ingested_at, now64(3, 'UTC')
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.us_session_coverage FINAL
) AS c
INNER JOIN (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.ref_instruments FINAL
) AS i ON i.instrument_id = c.instrument_id
INNER JOIN meta.migration_id_crosswalk AS ix FINAL
    ON ix.legacy_database = '{{source_database}}'
   AND ix.legacy_table = 'ref_instruments'
   AND ix.legacy_key = i.instrument_key AND ix.source_hash = i.migration_source_hash
   AND ix.target_kind = 'listing' AND ix.approved_at IS NOT NULL
INNER JOIN ref.listings AS l FINAL ON l.listing_id = ix.target_id AND l.country_code = 'US'
LEFT JOIN meta.session_coverage AS target FINAL
    ON target.source = c.source AND target.legacy_instrument_id = c.instrument_id
   AND target.resolution = c.resolution AND target.trade_date = c.trade_date
WHERE target.source_hash != c.migration_source_hash
   OR target.listing_id != l.listing_id;

INSERT INTO meta.recovery_state
SELECT
    'US', l.listing_id, s.instrument_id, s.symbol, s.source, s.resolution,
    s.history_complete, s.available_from, s.last_bar, s.checked_through,
    s.full_refreshed_at, s.error, s.migration_source_hash,
    greatest(s.version, target.version + 1), s.ingested_at, now64(3, 'UTC')
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.us_recovery_state FINAL
) AS s
INNER JOIN (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.ref_instruments FINAL
) AS i ON i.instrument_id = s.instrument_id
INNER JOIN meta.migration_id_crosswalk AS ix FINAL
    ON ix.legacy_database = '{{source_database}}'
   AND ix.legacy_table = 'ref_instruments'
   AND ix.legacy_key = i.instrument_key AND ix.source_hash = i.migration_source_hash
   AND ix.target_kind = 'listing' AND ix.approved_at IS NOT NULL
INNER JOIN ref.listings AS l FINAL ON l.listing_id = ix.target_id AND l.country_code = 'US'
LEFT JOIN meta.recovery_state AS target FINAL
    ON target.source = s.source AND target.legacy_instrument_id = s.instrument_id
   AND target.resolution = s.resolution
WHERE target.source_hash != s.migration_source_hash
   OR target.listing_id != l.listing_id;

INSERT INTO meta.source_status
SELECT
    'US', s.source, s.status, s.detail, s.checked_at,
    s.migration_source_hash, greatest(s.version, target.version + 1),
    now64(3, 'UTC')
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.us_source_status FINAL
) AS s
LEFT JOIN meta.source_status AS target FINAL
    ON target.country_code = 'US' AND target.source = s.source
WHERE target.source_hash != s.migration_source_hash;
