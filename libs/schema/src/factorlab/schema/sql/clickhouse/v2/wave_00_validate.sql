-- check: duplicate crosswalk targets
SELECT count() FROM (
    SELECT legacy_database, legacy_table, legacy_key, source_hash, target_kind
    FROM meta.migration_id_crosswalk
    GROUP BY legacy_database, legacy_table, legacy_key, source_hash, target_kind
    HAVING uniqExact(target_id) > 1
);

-- check: unapproved crosswalk rows
SELECT count() FROM meta.migration_id_crosswalk FINAL
WHERE approved_at IS NULL OR approved_by = '' OR evidence = '';

-- check: duplicate reference enrichment
SELECT count() FROM (
    SELECT legacy_database, legacy_table, legacy_key, source_hash, version
    FROM meta.migration_reference_enrichment
    GROUP BY legacy_database, legacy_table, legacy_key, source_hash, version
    HAVING count() > 1
);

-- check: unapproved reference enrichment
SELECT count() FROM meta.migration_reference_enrichment FINAL
WHERE approved_at IS NULL OR approved_by = '' OR evidence = '';

-- check: duplicate political enrichment
SELECT count() FROM (
    SELECT legacy_database, legacy_table, legacy_key, source_hash, version
    FROM meta.migration_political_trade_enrichment
    GROUP BY legacy_database, legacy_table, legacy_key, source_hash, version
    HAVING count() > 1
);

-- check: unapproved political enrichment
SELECT count() FROM meta.migration_political_trade_enrichment FINAL
WHERE approved_at IS NULL OR approved_by = '' OR evidence = '';

-- check: malformed source hashes
SELECT
    (SELECT count() FROM meta.migration_id_crosswalk FINAL WHERE NOT match(source_hash, '^[0-9a-f]{64}$'))
  + (SELECT count() FROM meta.migration_reference_enrichment FINAL WHERE NOT match(source_hash, '^[0-9a-f]{64}$'))
  + (SELECT count() FROM meta.migration_political_trade_enrichment FINAL WHERE NOT match(source_hash, '^[0-9a-f]{64}$'));
