CREATE VIEW IF NOT EXISTS research.bars AS
SELECT * FROM market.bars
WHERE as_of_time <= {asof_date:Date};

CREATE VIEW IF NOT EXISTS research.universe_membership AS
SELECT universe_id, listing_id, weight, effective_from, effective_to
FROM ref.universe_membership
WHERE effective_from <= {asof_date:Date}
  AND (effective_to IS NULL OR effective_to > {asof_date:Date});

