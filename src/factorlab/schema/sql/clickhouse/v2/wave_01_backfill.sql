-- Resolver-controlled Wave 1 backfill. No canonical UUID is generated here.

INSERT INTO ref.currencies
SELECT
    currency_code,
    currency_code AS name,
    currency_code NOT IN ('INR') AS is_deliverable,
    max(version) AS version,
    max(ingested_at) AS ingested_at
FROM meta.migration_reference_enrichment FINAL
WHERE approved_at IS NOT NULL
  AND currency_code NOT IN (SELECT currency_code FROM ref.currencies FINAL)
GROUP BY currency_code;

INSERT INTO ref.countries
SELECT
    c.country_code,
    argMax(c.name, c.version),
    argMax(c.region, c.version),
    argMax(e.currency_code, e.version),
    argMax(c.timezone, c.version),
    true,
    toDate('1970-01-01'),
    max(c.version),
    max(c.ingested_at)
FROM {{source_database}}.ref_countries AS c FINAL
INNER JOIN meta.migration_reference_enrichment AS e FINAL
    ON e.legacy_database = '{{source_database}}'
   AND e.legacy_table = 'ref_countries'
   AND e.legacy_key = c.country_code
   AND e.approved_at IS NOT NULL
LEFT JOIN ref.countries AS target FINAL ON target.country_code = c.country_code
WHERE target.country_code = '' OR c.version > target.version
GROUP BY c.country_code;

INSERT INTO ref.exchanges
SELECT
    x.exchange_code,
    argMax(e.mic, e.version),
    argMax(x.name, x.version),
    argMax(x.country_code, x.version),
    argMax(e.currency_code, e.version),
    argMax(e.session_timezone, e.version),
    concat('{"regular":{"open":"', argMax(e.regular_open, e.version),
           '","close":"', argMax(e.regular_close, e.version), '"}}'),
    true,
    max(x.version),
    max(x.ingested_at)
FROM {{source_database}}.ref_exchanges AS x FINAL
INNER JOIN meta.migration_reference_enrichment AS e FINAL
    ON e.legacy_database = '{{source_database}}'
   AND e.legacy_table = 'ref_exchanges'
   AND e.legacy_key = x.exchange_code
   AND e.approved_at IS NOT NULL
LEFT JOIN ref.exchanges AS target FINAL ON target.exchange_code = x.exchange_code
WHERE target.exchange_code = '' OR x.version > target.version
GROUP BY x.exchange_code;

INSERT INTO ref.sources
SELECT source, 'migration', source, 'none', ['*'], true, '', version, ingested_at
FROM (
    SELECT source, max(version) AS version, max(ingested_at) AS ingested_at
    FROM (
        SELECT source, version, ingested_at FROM {{source_database}}.ref_instruments FINAL
        UNION ALL
        SELECT source, version, ingested_at FROM {{source_database}}.ref_contracts FINAL
        UNION ALL
        SELECT source, toUInt64(toUnixTimestamp64Milli(fetched_at)) AS version, fetched_at AS ingested_at
        FROM {{source_database}}.raw_http_archive
    )
    GROUP BY source
) AS legacy
LEFT JOIN ref.sources AS target FINAL ON target.source_id = legacy.source
WHERE target.source_id = '' OR legacy.version > target.version;

INSERT INTO ref.identifier_aliases
SELECT
    'upstox_instrument_key',
    i.instrument_key,
    toNullable(i.country_code),
    toNullable(i.exchange_code),
    'listing',
    x.target_id,
    i.source,
    i.first_seen,
    if(i.status = 'active', NULL, toNullable(i.last_seen)),
    CAST('manual_override', 'Enum8(\'exact\'=1,\'high\'=2,\'medium\'=3,\'low\'=4,\'manual_override\'=5)'),
    x.evidence,
    greatest(i.version, x.version),
    greatest(i.ingested_at, x.ingested_at)
FROM {{source_database}}.ref_instruments AS i FINAL
INNER JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = 'ref_instruments'
   AND x.legacy_key = i.instrument_key
   AND x.target_kind = 'listing'
   AND x.approved_at IS NOT NULL
LEFT JOIN ref.identifier_aliases AS target FINAL
    ON target.alias_kind = 'upstox_instrument_key'
   AND target.alias_value = i.instrument_key
   AND target.valid_from = i.first_seen
WHERE target.alias_value = '' OR greatest(i.version, x.version) > target.version;

INSERT INTO ref.legislator_terms
SELECT
    x.target_id,
    l.bioguide_id,
    toUInt16(floor((toYear(l.term_start) - 1789) / 2) + 1),
    lower(l.chamber),
    l.state,
    l.district,
    l.party,
    NULL,
    l.term_start,
    l.term_end,
    l.in_office,
    l.source,
    toNullable(l.raw_id),
    l.ingested_at,
    greatest(l.version, x.version),
    greatest(l.ingested_at, x.ingested_at)
FROM {{source_database}}.alt_political_legislators AS l FINAL
INNER JOIN meta.migration_id_crosswalk AS x FINAL
    ON x.legacy_database = '{{source_database}}'
   AND x.legacy_table = 'alt_political_legislators'
   AND x.legacy_key = l.bioguide_id
   AND x.target_kind = 'entity'
   AND x.approved_at IS NOT NULL
LEFT JOIN ref.legislator_terms AS target FINAL
    ON target.bioguide_id = l.bioguide_id AND target.term_start = l.term_start
WHERE target.bioguide_id = '' OR greatest(l.version, x.version) > target.version;

INSERT INTO raw.archive
SELECT
    a.raw_id, a.source, a.source, 'http', NULL, a.source_url, a.fetch_key,
    toNullable(a.status_code), a.response_headers, a.response_body, a.content_type,
    a.content_encoding, a.response_sha256, a.fetched_at, NULL, NULL,
    a.as_of_time, a.metadata_json
FROM {{source_database}}.raw_http_archive AS a
LEFT JOIN raw.archive AS target ON target.raw_id = a.raw_id
WHERE target.raw_id = toUUID('00000000-0000-0000-0000-000000000000');

