-- check: dangling crosswalk targets
SELECT count() FROM meta.migration_id_crosswalk AS x FINAL
LEFT JOIN ref.entities AS e FINAL ON x.target_kind = 'entity' AND x.target_id = e.entity_id
LEFT JOIN ref.securities AS s FINAL ON x.target_kind = 'security' AND x.target_id = s.security_id
LEFT JOIN ref.listings AS l FINAL ON x.target_kind = 'listing' AND x.target_id = l.listing_id
LEFT JOIN ref.contracts AS c FINAL ON x.target_kind = 'contract' AND x.target_id = c.contract_id
WHERE x.target_kind IN ('entity', 'security', 'listing', 'contract')
  AND e.entity_id = toUUID('00000000-0000-0000-0000-000000000000')
  AND s.security_id = toUUID('00000000-0000-0000-0000-000000000000')
  AND l.listing_id = toUUID('00000000-0000-0000-0000-000000000000')
  AND c.contract_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: unknown enrichment exchanges
SELECT count() FROM meta.migration_reference_enrichment AS r FINAL
LEFT JOIN ref.exchanges AS e FINAL ON r.exchange_code = e.exchange_code
WHERE r.exchange_code != ''
  AND e.exchange_code = '';

-- check: unknown enrichment currencies
SELECT count() FROM meta.migration_reference_enrichment AS r FINAL
LEFT JOIN ref.currencies AS c FINAL ON r.currency_code = c.currency_code
WHERE c.currency_code = '';

-- check: inconsistent listing crosswalk kinds
SELECT count() FROM meta.migration_id_crosswalk FINAL
WHERE legacy_table = 'ref_instruments' AND target_kind != 'listing';

-- check: legacy instruments lack approved listing mappings
SELECT count()
FROM {{source_database}}.ref_instruments AS i FINAL
LEFT JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = 'ref_instruments'
   AND x.legacy_key = i.instrument_key AND x.target_kind = 'listing'
   AND x.approved_at IS NOT NULL
WHERE x.target_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: legacy contracts lack approved contract mappings
SELECT count()
FROM {{source_database}}.ref_contracts AS c FINAL
LEFT JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = 'ref_contracts'
   AND x.legacy_key = c.contract_key AND x.target_kind = 'contract'
   AND x.approved_at IS NOT NULL
WHERE x.target_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: legacy reference rows lack approved enrichment
SELECT
    (SELECT count()
     FROM {{source_database}}.ref_countries AS c FINAL
     LEFT JOIN meta.migration_reference_enrichment AS x FINAL
       ON x.legacy_database = '{{source_database}}' AND x.legacy_table = 'ref_countries'
      AND x.legacy_key = c.country_code AND x.approved_at IS NOT NULL
     WHERE x.legacy_key = '')
  + (SELECT count()
     FROM {{source_database}}.ref_exchanges AS e FINAL
     LEFT JOIN meta.migration_reference_enrichment AS x FINAL
       ON x.legacy_database = '{{source_database}}' AND x.legacy_table = 'ref_exchanges'
      AND x.legacy_key = e.exchange_code AND x.approved_at IS NOT NULL
     WHERE x.legacy_key = '');

-- check: stale reference crosswalk source hashes
SELECT count()
FROM (
    SELECT 'ref_instruments' AS legacy_table, instrument_key AS legacy_key,
           lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
    FROM {{source_database}}.ref_instruments FINAL
    UNION ALL
    SELECT 'ref_contracts', contract_key,
           lower(hex(SHA256(toJSONString(tuple(*)))))
    FROM {{source_database}}.ref_contracts FINAL
    UNION ALL
    SELECT 'alt_political_legislators', bioguide_id,
           lower(hex(SHA256(toJSONString(tuple(*)))))
    FROM {{source_database}}.alt_political_legislators FINAL
) AS legacy
LEFT JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = legacy.legacy_table
   AND x.legacy_key = legacy.legacy_key
   AND x.source_hash = legacy.source_hash
   AND x.approved_at IS NOT NULL
WHERE x.target_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: stale reference enrichment source hashes
SELECT count()
FROM (
    SELECT 'ref_countries' AS legacy_table, toString(country_code) AS legacy_key,
           lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
    FROM {{source_database}}.ref_countries FINAL
    UNION ALL
    SELECT 'ref_exchanges', toString(exchange_code),
           lower(hex(SHA256(toJSONString(tuple(*)))))
    FROM {{source_database}}.ref_exchanges FINAL
) AS legacy
LEFT JOIN meta.migration_reference_enrichment AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = legacy.legacy_table
   AND x.legacy_key = legacy.legacy_key
   AND x.source_hash = legacy.source_hash
   AND x.approved_at IS NOT NULL
WHERE x.legacy_key = '';

-- check: unsupported legacy exchange timezones
SELECT count() FROM meta.migration_reference_enrichment FINAL
WHERE legacy_table = 'ref_exchanges'
  AND session_timezone NOT IN ('America/New_York', 'Asia/Kolkata', 'UTC');
