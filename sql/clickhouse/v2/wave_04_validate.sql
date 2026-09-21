-- check: internally inconsistent political amounts
SELECT count() FROM meta.migration_political_trade_enrichment FINAL
WHERE (amount_min IS NOT NULL AND amount_max IS NOT NULL AND amount_min > amount_max)
   OR amount_bucket_id = '';

-- check: political source rows lack approved corrections
SELECT
    (SELECT count()
     FROM {{source_database}}.alt_political_trades AS t FINAL
     LEFT JOIN meta.migration_political_trade_enrichment AS x FINAL
       ON x.legacy_database = '{{source_database}}' AND x.legacy_table = 'alt_political_trades'
      AND x.legacy_key = t.trade_key AND x.approved_at IS NOT NULL
     WHERE x.legacy_key = '')
  + (SELECT count()
     FROM {{source_database}}.alt_political_house_filings AS f FINAL
     LEFT JOIN meta.migration_political_trade_enrichment AS x FINAL
       ON x.legacy_database = '{{source_database}}'
      AND x.legacy_table = 'alt_political_house_filings'
      AND x.legacy_key = f.filing_id AND x.approved_at IS NOT NULL
     WHERE x.legacy_key = '');

-- check: stale political enrichment source hashes
SELECT count()
FROM meta.migration_political_trade_enrichment AS x FINAL
INNER JOIN (
    SELECT 'alt_political_trades' AS legacy_table, toString(trade_key) AS legacy_key,
           lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
    FROM {{source_database}}.alt_political_trades FINAL
    UNION ALL
    SELECT 'alt_political_house_filings', filing_id,
           lower(hex(SHA256(toJSONString(tuple(*)))))
    FROM {{source_database}}.alt_political_house_filings FINAL
) AS legacy
    ON x.legacy_table = legacy.legacy_table AND x.legacy_key = legacy.legacy_key
WHERE x.legacy_database = '{{source_database}}' AND x.source_hash != legacy.source_hash;

-- check: committee entities lack approved canonical mappings
SELECT count()
FROM {{source_database}}.alt_political_committees AS c FINAL
LEFT JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = 'alt_political_committees'
   AND x.legacy_key = c.committee_id AND x.target_kind = 'entity'
   AND x.approved_at IS NOT NULL
WHERE x.target_id = toUUID('00000000-0000-0000-0000-000000000000');

-- check: membership legislators lack approved canonical mappings
SELECT count() FROM (
    SELECT DISTINCT m.bioguide_id
    FROM {{source_database}}.alt_political_committee_memberships AS m FINAL
    LEFT JOIN meta.migration_id_crosswalk AS x FINAL
        ON x.legacy_database = '{{source_database}}'
       AND x.legacy_table = 'alt_political_legislators'
       AND x.legacy_key = m.bioguide_id AND x.target_kind = 'entity'
       AND x.approved_at IS NOT NULL
    WHERE x.target_id = toUUID('00000000-0000-0000-0000-000000000000')
);

-- check: stale political canonical-ID source hashes
SELECT count()
FROM meta.migration_id_crosswalk AS x FINAL
INNER JOIN (
    SELECT 'alt_political_trades' AS legacy_table, toString(trade_key) AS legacy_key,
           lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
    FROM {{source_database}}.alt_political_trades FINAL
    UNION ALL
    SELECT 'alt_political_committees', committee_id,
           lower(hex(SHA256(toJSONString(tuple(*)))))
    FROM {{source_database}}.alt_political_committees FINAL
) AS legacy
    ON x.legacy_table = legacy.legacy_table AND x.legacy_key = legacy.legacy_key
WHERE x.legacy_database = '{{source_database}}' AND x.source_hash != legacy.source_hash;

-- check: dangling political canonical IDs
SELECT count() FROM meta.migration_political_trade_enrichment AS p FINAL
LEFT JOIN ref.entities AS le FINAL ON p.legislator_entity_id = le.entity_id
LEFT JOIN ref.listings AS l FINAL ON p.listing_id = l.listing_id
LEFT JOIN ref.securities AS s FINAL ON p.security_id = s.security_id
LEFT JOIN ref.entities AS ie FINAL ON p.entity_id = ie.entity_id
LEFT JOIN ref.contracts AS c FINAL ON p.contract_id = c.contract_id
WHERE (p.legislator_entity_id IS NOT NULL AND le.entity_id = toUUID('00000000-0000-0000-0000-000000000000'))
   OR (p.listing_id IS NOT NULL AND l.listing_id = toUUID('00000000-0000-0000-0000-000000000000'))
   OR (p.security_id IS NOT NULL AND s.security_id = toUUID('00000000-0000-0000-0000-000000000000'))
   OR (p.entity_id IS NOT NULL AND ie.entity_id = toUUID('00000000-0000-0000-0000-000000000000'))
   OR (p.contract_id IS NOT NULL AND c.contract_id = toUUID('00000000-0000-0000-0000-000000000000'));

-- check: listing and security disagree
SELECT count() FROM meta.migration_political_trade_enrichment AS p FINAL
INNER JOIN ref.listings AS l FINAL ON p.listing_id = l.listing_id
WHERE p.security_id IS NOT NULL AND p.security_id != l.security_id;

-- check: resolved legislators are not legislator entities
SELECT count() FROM meta.migration_political_trade_enrichment AS p FINAL
INNER JOIN ref.entities AS e FINAL ON p.legislator_entity_id = e.entity_id
WHERE e.entity_type != 'person_legislator';

-- check: bioguide resolution below 95 percent
SELECT toUInt8(count() > 0 AND countIf(bioguide_id IS NOT NULL) / count() < 0.95)
FROM meta.migration_political_trade_enrichment FINAL
WHERE legacy_table = 'alt_political_trades';

-- check: listing resolution below 85 percent for listing-addressable assets
SELECT toUInt8(count() > 0 AND countIf(listing_id IS NOT NULL) / count() < 0.85)
FROM meta.migration_political_trade_enrichment FINAL
WHERE legacy_table = 'alt_political_trades'
  AND security_type IN ('common', 'preferred', 'adr', 'etf', 'reit', 'warrant');

-- check: OCC-parseable options are unresolved
SELECT count() FROM meta.migration_political_trade_enrichment FINAL
WHERE legacy_table = 'alt_political_trades'
  AND security_type = 'option'
  AND contract_id IS NULL;

-- check: political membership SCD periods overlap
SELECT count() FROM (
    SELECT
        committee_id,
        bioguide_id,
        effective_from,
        effective_to,
        lagInFrame(effective_to) OVER (
            PARTITION BY committee_id, bioguide_id ORDER BY effective_from
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS previous_to
    FROM alt.political_committee_memberships FINAL
)
WHERE previous_to IS NOT NULL AND previous_to > effective_from;

-- check: political fact dangling FK ratio at or above 0.5 percent
SELECT toUInt8(count() > 0 AND countIf(
    (t.listing_id IS NOT NULL AND l.listing_id = toUUID('00000000-0000-0000-0000-000000000000'))
 OR (t.security_id IS NOT NULL AND s.security_id = toUUID('00000000-0000-0000-0000-000000000000'))
 OR (t.legislator_entity_id IS NOT NULL AND e.entity_id = toUUID('00000000-0000-0000-0000-000000000000'))
) / count() >= 0.005)
FROM alt.political_trades AS t FINAL
LEFT JOIN ref.listings AS l FINAL ON t.listing_id = l.listing_id
LEFT JOIN ref.securities AS s FINAL ON t.security_id = s.security_id
LEFT JOIN ref.entities AS e FINAL ON t.legislator_entity_id = e.entity_id;
