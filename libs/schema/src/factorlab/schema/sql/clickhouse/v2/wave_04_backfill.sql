-- Political facts are admitted only through approved corrections.

INSERT INTO alt.political_filings
SELECT
    f.filing_id, 'US', 'house', f.filing_type, f.filing_year, f.filing_date,
    f.filer_name_raw, p.bioguide_id, p.legislator_entity_id, p.bioguide_confidence,
    f.filing_url, NULL, NULL, 0, false, false,
    toUInt32(coalesce(tc.trade_count, 0)),
    f.source, 'house_clerk_ptr', toNullable(f.raw_id), toUUID('{{migration_run_id}}'),
    f.as_of_time, f.ingested_at, greatest(f.version, p.version)
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.alt_political_house_filings FINAL
) AS f
INNER JOIN meta.migration_political_trade_enrichment AS p FINAL
    ON p.legacy_database = '{{source_database}}'
   AND p.legacy_table = 'alt_political_house_filings'
   AND p.legacy_key = f.filing_id
   AND p.source_hash = f.migration_source_hash AND p.approved_at IS NOT NULL
LEFT JOIN (
    SELECT filing_id, count() AS trade_count
    FROM {{source_database}}.alt_political_trades FINAL
    GROUP BY filing_id
) AS tc ON tc.filing_id = f.filing_id
LEFT JOIN alt.political_filings AS target FINAL ON target.filing_id = f.filing_id
WHERE target.filing_id = '' OR greatest(f.version, p.version) > target.version;

INSERT INTO alt.political_trades
SELECT
    x.target_id, t.country_code, lower(t.chamber), t.filing_id, t.filing_url,
    t.filing_date, t.transaction_date, t.notification_date, t.legislator_name,
    p.bioguide_id, p.legislator_entity_id, p.bioguide_confidence,
    t.ticker, t.asset_name_raw, t.asset_type_code, p.security_type,
    p.listing_id, p.contract_id, p.security_id, p.entity_id, p.asset_resolution_confidence,
    t.transaction_type, t.owner_code, t.filer_type, p.amount_bucket_id,
    p.amount_min, p.amount_max, p.amount_currency, t.amount_str,
    t.source, 'house_clerk_ptr', t.parser_version, toNullable(t.raw_id),
    toUUID('{{migration_run_id}}'), t.as_of_time, t.ingested_at,
    greatest(t.version, p.version, x.version)
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.alt_political_trades FINAL
) AS t
INNER JOIN meta.migration_political_trade_enrichment AS p FINAL
    ON p.legacy_database = '{{source_database}}'
   AND p.legacy_table = 'alt_political_trades'
   AND p.legacy_key = t.trade_key
   AND p.source_hash = t.migration_source_hash AND p.approved_at IS NOT NULL
INNER JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = 'alt_political_trades'
   AND x.legacy_key = t.trade_key AND x.source_hash = t.migration_source_hash
   AND x.target_kind = 'political_trade'
   AND x.approved_at IS NOT NULL
LEFT JOIN alt.political_trades AS target FINAL ON target.political_trade_id = x.target_id
WHERE target.political_trade_id = toUUID('00000000-0000-0000-0000-000000000000')
   OR greatest(t.version, p.version, x.version) > target.version;

INSERT INTO meta.unresolved_entities
SELECT
    u.first_seen, u.last_seen, u.source, 'political_asset', u.alias_value,
    toNullable('US'), NULL, u.context_json, u.occurrence_count,
    0, NULL, NULL, NULL, NULL, NULL, NULL, u.version, u.ingested_at
FROM (
    SELECT
        min(t.ingested_at) AS first_seen,
        max(t.ingested_at) AS last_seen,
        t.source AS source,
        coalesce(nullIf(t.ticker, ''), t.asset_name_raw) AS alias_value,
        concat('{"trade_keys":', toJSONString(groupArray(toString(t.trade_key))), '}') AS context_json,
        toUInt32(count()) AS occurrence_count,
        max(greatest(t.version, p.version)) AS version,
        max(t.ingested_at) AS ingested_at
    FROM (
        SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
        FROM {{source_database}}.alt_political_trades FINAL
    ) AS t
    INNER JOIN meta.migration_political_trade_enrichment AS p FINAL
        ON p.legacy_database = '{{source_database}}'
       AND p.legacy_table = 'alt_political_trades'
       AND p.legacy_key = t.trade_key
       AND p.source_hash = t.migration_source_hash AND p.approved_at IS NOT NULL
    WHERE p.asset_resolution_confidence = 'unresolved'
    GROUP BY t.source, alias_value
) AS u
LEFT JOIN meta.unresolved_entities AS target FINAL
    ON target.source = u.source
   AND target.alias_kind = 'political_asset'
   AND target.alias_value = u.alias_value
WHERE target.alias_value = '' OR u.version > target.version;

INSERT INTO alt.political_committees
SELECT
    c.committee_id, x.target_id, 'US', toUInt16(coalesce(max(m.congress_number), 0)),
    c.parent_committee_id, lower(c.chamber), c.name, c.jurisdiction, c.url,
    c.is_subcommittee, toDate('1970-01-01'),
    if(c.is_current, NULL, toNullable(max(m.snapshot_date))),
    c.source, toNullable(c.raw_id), toUUID('{{migration_run_id}}'),
    c.ingested_at, c.ingested_at, greatest(c.version, x.version)
FROM (
    SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS migration_source_hash
    FROM {{source_database}}.alt_political_committees FINAL
) AS c
INNER JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}' AND x.legacy_table = 'alt_political_committees'
   AND x.legacy_key = c.committee_id AND x.source_hash = c.migration_source_hash
   AND x.target_kind = 'entity'
   AND x.approved_at IS NOT NULL
LEFT JOIN {{source_database}}.alt_political_committee_memberships AS m FINAL
    ON m.committee_id = c.committee_id
LEFT JOIN alt.political_committees AS target FINAL
    ON target.committee_id = c.committee_id AND target.effective_from = toDate('1970-01-01')
WHERE target.committee_id = '' OR greatest(c.version, x.version) > target.version
GROUP BY
    c.committee_id, x.target_id, c.parent_committee_id, c.chamber, c.name,
    c.jurisdiction, c.url, c.is_subcommittee, c.is_current, c.source, c.raw_id,
    c.ingested_at, c.version, x.version;

INSERT INTO alt.political_committee_memberships
WITH
    snapshot_index AS (
        SELECT
            snapshot_date,
            snapshot_ingested_at,
            row_number() OVER (ORDER BY snapshot_date) AS snapshot_idx,
            max(snapshot_date) OVER () AS latest_snapshot
        FROM (
            SELECT snapshot_date, max(ingested_at) AS snapshot_ingested_at
            FROM {{source_database}}.alt_political_committee_memberships FINAL
            GROUP BY snapshot_date
        )
    ),
    observed AS (
        SELECT
            m.*,
            d.snapshot_idx,
            d.latest_snapshot,
            row_number() OVER (
                PARTITION BY m.congress_number, m.committee_id, m.bioguide_id,
                             m.role, m.majority_status, m.rank
                ORDER BY d.snapshot_idx
            ) AS state_sequence
        FROM {{source_database}}.alt_political_committee_memberships AS m FINAL
        INNER JOIN snapshot_index AS d ON d.snapshot_date = m.snapshot_date
    ),
    periods AS (
        SELECT
            congress_number, committee_id, bioguide_id, role, majority_status, rank,
            min(snapshot_date) AS effective_from,
            max(snapshot_date) AS end_snapshot,
            max(snapshot_idx) AS end_idx,
            max(latest_snapshot) AS latest_snapshot,
            argMax(source, snapshot_date) AS source,
            argMax(raw_id, snapshot_date) AS raw_id,
            max(version) AS version,
            max(ingested_at) AS ingested_at
        FROM observed
        GROUP BY
            congress_number, committee_id, bioguide_id, role, majority_status, rank,
            snapshot_idx - state_sequence
    )
SELECT
    'US', p.congress_number, p.committee_id, cm.target_id, p.bioguide_id, lm.target_id,
    p.role, p.majority_status, p.rank, p.effective_from,
    if(p.end_snapshot = p.latest_snapshot, NULL, toNullable(next_snapshot.snapshot_date)),
    p.source, toNullable(p.raw_id), toUUID('{{migration_run_id}}'), p.ingested_at,
    p.ingested_at,
    greatest(
        p.version, cm.version, lm.version,
        toUInt64(toUnixTimestamp64Nano(p.ingested_at)),
        if(p.end_snapshot = p.latest_snapshot, toUInt64(0),
           toUInt64(toUnixTimestamp64Nano(next_snapshot.snapshot_ingested_at)))
    )
FROM periods AS p
LEFT JOIN snapshot_index AS next_snapshot ON next_snapshot.snapshot_idx = p.end_idx + 1
INNER JOIN (
    SELECT committee_id, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
    FROM {{source_database}}.alt_political_committees FINAL
) AS current_committee ON current_committee.committee_id = p.committee_id
INNER JOIN (
    SELECT bioguide_id, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
    FROM {{source_database}}.alt_political_legislators FINAL
) AS current_legislator ON current_legislator.bioguide_id = p.bioguide_id
INNER JOIN meta.migration_id_crosswalk AS cm FINAL
    ON cm.legacy_database = '{{source_database}}'
   AND cm.legacy_table = 'alt_political_committees'
   AND cm.legacy_key = p.committee_id
   AND cm.source_hash = current_committee.source_hash AND cm.target_kind = 'entity'
   AND cm.approved_at IS NOT NULL
INNER JOIN meta.migration_id_crosswalk AS lm FINAL
    ON lm.legacy_database = '{{source_database}}'
   AND lm.legacy_table = 'alt_political_legislators'
   AND lm.legacy_key = p.bioguide_id
   AND lm.source_hash = current_legislator.source_hash AND lm.target_kind = 'entity'
   AND lm.approved_at IS NOT NULL
LEFT JOIN alt.political_committee_memberships AS target FINAL
    ON target.committee_id = p.committee_id
   AND target.bioguide_id = p.bioguide_id
   AND target.effective_from = p.effective_from
WHERE target.committee_id = ''
   OR greatest(
        p.version, cm.version, lm.version,
        toUInt64(toUnixTimestamp64Nano(p.ingested_at)),
        if(p.end_snapshot = p.latest_snapshot, toUInt64(0),
           toUInt64(toUnixTimestamp64Nano(next_snapshot.snapshot_ingested_at)))
   ) > target.version;
