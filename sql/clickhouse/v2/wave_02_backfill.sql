-- Market candles become canonical listing-keyed bars. The migration run is lineage,
-- not a canonical business identifier.

INSERT INTO market.bars
SELECT
    l.country_code, l.listing_id, l.security_id, s.entity_id, s.security_type,
    '1min',
    if(multiIf(
           e.timezone = 'America/New_York', formatDateTime(c.bar_time, '%H:%M', 'America/New_York'),
           e.timezone = 'Asia/Kolkata', formatDateTime(c.bar_time, '%H:%M', 'Asia/Kolkata'),
           formatDateTime(c.bar_time, '%H:%M', 'UTC')) >= r.regular_open
       AND multiIf(
           e.timezone = 'America/New_York', formatDateTime(c.bar_time, '%H:%M', 'America/New_York'),
           e.timezone = 'Asia/Kolkata', formatDateTime(c.bar_time, '%H:%M', 'Asia/Kolkata'),
           formatDateTime(c.bar_time, '%H:%M', 'UTC')) < r.regular_close,
       'regular', 'extended'),
    c.bar_time,
    multiIf(
        e.timezone = 'America/New_York', toDate(c.bar_time, 'America/New_York'),
        e.timezone = 'Asia/Kolkata', toDate(c.bar_time, 'Asia/Kolkata'),
        toDate(c.bar_time, 'UTC')),
    c.open, c.high, c.low, c.close, c.volume, NULL, NULL, c.oi, NULL,
    c.source, c.source, c.raw_id, toUUID('{{migration_run_id}}'),
    c.as_of_time, c.ingested_at,
    toNullable(toInt32(dateDiff('millisecond', c.as_of_time, c.ingested_at))), c.version
FROM {{source_database}}.market_candles_1min AS c FINAL
INNER JOIN {{source_database}}.ref_instruments AS i FINAL ON i.instrument_id = c.instrument_id
INNER JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}' AND x.legacy_table = 'ref_instruments'
   AND x.legacy_key = i.instrument_key AND x.target_kind = 'listing' AND x.approved_at IS NOT NULL
INNER JOIN ref.listings AS l FINAL ON l.listing_id = x.target_id
INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
INNER JOIN ref.exchanges AS e FINAL ON e.exchange_code = l.exchange_code
INNER JOIN meta.migration_reference_enrichment AS r FINAL
    ON r.legacy_database = '{{source_database}}' AND r.legacy_table = 'ref_exchanges'
   AND r.legacy_key = l.exchange_code AND r.approved_at IS NOT NULL
LEFT JOIN market.bars AS target FINAL
    ON target.listing_id = l.listing_id AND target.resolution = '1min'
   AND target.source = c.source AND target.bar_time = c.bar_time
WHERE target.listing_id = toUUID('00000000-0000-0000-0000-000000000000')
   OR c.version > target.version;

INSERT INTO market.bars
SELECT
    l.country_code, l.listing_id, l.security_id, s.entity_id, s.security_type,
    'daily', 'regular',
    multiIf(
        e.timezone = 'America/New_York', toDateTime64(c.trade_date, 3, 'America/New_York'),
        e.timezone = 'Asia/Kolkata', toDateTime64(c.trade_date, 3, 'Asia/Kolkata'),
        toDateTime64(c.trade_date, 3, 'UTC')),
    c.trade_date,
    c.open, c.high, c.low, c.close, c.volume, NULL, NULL, NULL, NULL,
    c.source, c.source, c.raw_id, toUUID('{{migration_run_id}}'),
    c.as_of_time, c.ingested_at,
    toNullable(toInt32(dateDiff('millisecond', c.as_of_time, c.ingested_at))), c.version
FROM {{source_database}}.market_candles_daily AS c FINAL
INNER JOIN {{source_database}}.ref_instruments AS i FINAL ON i.instrument_id = c.instrument_id
INNER JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}' AND x.legacy_table = 'ref_instruments'
   AND x.legacy_key = i.instrument_key AND x.target_kind = 'listing' AND x.approved_at IS NOT NULL
INNER JOIN ref.listings AS l FINAL ON l.listing_id = x.target_id
INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
INNER JOIN ref.exchanges AS e FINAL ON e.exchange_code = l.exchange_code
LEFT JOIN market.bars AS target FINAL
    ON target.listing_id = l.listing_id AND target.resolution = 'daily'
   AND target.source = c.source AND target.trade_date = c.trade_date
WHERE target.listing_id = toUUID('00000000-0000-0000-0000-000000000000')
   OR c.version > target.version;
