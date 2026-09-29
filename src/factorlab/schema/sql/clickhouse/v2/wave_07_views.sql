CREATE VIEW IF NOT EXISTS research.owned_listings AS
SELECT
    listing_id,
    argMax(security_id, snapshot_time) AS security_id,
    argMax(entity_id, snapshot_time) AS entity_id,
    argMax(trading_symbol, snapshot_time) AS trading_symbol,
    argMax(position, snapshot_time) AS position,
    argMax(market_value, snapshot_time) AS market_value,
    max(snapshot_time) AS as_of
FROM broker.positions_snapshot AS p
WHERE p.listing_id IS NOT NULL
  AND p.account_mode = 'live'
  AND p.position != 0
GROUP BY p.listing_id;
