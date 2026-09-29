CREATE VIEW IF NOT EXISTS research.political_trades AS
SELECT * FROM alt.political_trades
WHERE as_of_time <= {asof_date:Date};

