-- Wave 10: read-time multi-vendor bar selection (06 §13.1 rev 12, 07 §10).
-- Every bound vendor keeps its own market.bars rows (source is in the sort key);
-- this view returns one row per (country, listing, resolution, bar_time), taking
-- each field from the lowest-priority-number active primary/secondary source in
-- ref.source_priorities. Shadow and disabled sources are never selected.
-- Always filter by listing_id and a bar_time range: the view aggregates.
CREATE VIEW IF NOT EXISTS market.bars_best AS
SELECT
    b.country_code AS country_code,
    b.listing_id AS listing_id,
    b.resolution AS resolution,
    b.bar_time AS bar_time,
    argMin(b.security_id, p.priority) AS security_id,
    argMin(b.entity_id, p.priority) AS entity_id,
    argMin(b.product_type, p.priority) AS product_type,
    argMin(b.session, p.priority) AS session,
    argMin(b.trade_date, p.priority) AS trade_date,
    argMin(b.open, p.priority) AS open,
    argMin(b.high, p.priority) AS high,
    argMin(b.low, p.priority) AS low,
    argMin(b.close, p.priority) AS close,
    argMin(b.volume, p.priority) AS volume,
    argMin(b.turnover, p.priority) AS turnover,
    argMin(b.trades_count, p.priority) AS trades_count,
    argMin(b.oi, p.priority) AS oi,
    argMin(b.settlement_price, p.priority) AS settlement_price,
    argMin(b.source, p.priority) AS chosen_source,
    argMin(b.source_channel, p.priority) AS chosen_source_channel,
    argMin(b.raw_id, p.priority) AS raw_id,
    argMin(b.ingest_run_id, p.priority) AS ingest_run_id,
    argMin(b.as_of_time, p.priority) AS as_of_time,
    min(p.priority) AS chosen_priority,
    count() AS source_count
FROM market.bars AS b FINAL
INNER JOIN (
    SELECT country_code, resolution, source, priority
    FROM ref.source_priorities FINAL
    WHERE dataset = 'market.bars' AND active AND role IN ('primary', 'secondary')
) AS p
    ON p.country_code = b.country_code
   AND p.resolution = b.resolution
   AND p.source = b.source
GROUP BY b.country_code, b.listing_id, b.resolution, b.bar_time;
