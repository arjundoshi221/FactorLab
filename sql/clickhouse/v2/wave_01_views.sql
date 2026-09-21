CREATE VIEW IF NOT EXISTS ref.session_windows_effective AS
SELECT
    e.exchange_code,
    d.trade_date,
    s.session,
    coalesce(h.override_open, s.starts_at) AS starts_at,
    coalesce(h.override_close, s.ends_at) AS ends_at,
    dateDiff(
        'minute',
        parseDateTimeBestEffort(concat(toString(d.trade_date), ' ', coalesce(h.override_open, s.starts_at))),
        parseDateTimeBestEffort(concat(toString(d.trade_date), ' ', coalesce(h.override_close, s.ends_at)))
    ) AS expected_bars_1min,
    coalesce(h.market_state, 'regular') AS market_state
FROM ref.exchanges AS e
CROSS JOIN (SELECT toDate('2000-01-01') + number AS trade_date FROM numbers(50000)) AS d
LEFT JOIN ref.sessions AS s
    ON s.exchange_code = e.exchange_code
   AND s.day_of_week = toDayOfWeek(d.trade_date) - 1
   AND s.effective_from <= d.trade_date
   AND (s.effective_to IS NULL OR s.effective_to > d.trade_date)
LEFT JOIN ref.holidays AS h
    ON h.exchange_code = e.exchange_code AND h.holiday_date = d.trade_date
WHERE s.session IS NOT NULL AND coalesce(h.market_state, '') != 'closed';

