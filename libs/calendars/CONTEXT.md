# factorlab-calendars

> Exchange session calendars (XNYS, XBOM) and trading-hours windows on top of `exchange_calendars`.

## Purpose

A small library that answers "is this a trading session, and when does it open and
close?" for daemons, providers, storage and the API. It sits one layer above
`factorlab-core` (`LIBRARY_LAYERS["calendars"] = {"core"}` in
[test_boundaries.py](../../tests/architecture/test_boundaries.py)) and is one of the
three libraries providers may import (rule R8).

## Owns and does not own

- Owns: the XNYS calendar helpers in [us.py](src/factorlab/calendars/us.py) and the
  generic `MarketWindow` in [window.py](src/factorlab/calendars/window.py).
- Does not own: session tagging and `trade_date` keys written to ClickHouse
  (`factorlab-storage`, `storage/sinks/calendar.py`), the per-market session gate of
  the engine daemon (`MARKET_WINDOWS` in `factorlab-orchestration`), daemon schedules
  (each component).

## Entry points

None (library). Public API:

- `factorlab.calendars.us`: `NY` (America/New_York), `calendar()` (cached XNYS
  calendar from 1970 to two years ahead), `is_session(day)`, `bounds(day)` (session
  open/close datetimes or `None`), `latest_completed(now, grace_minutes=30)`,
  `next_scheduled_run(now, at, horizon_days=14)` (next New York wall-clock slot on a
  session, DST-aware; `now` must be tz-aware).
- `factorlab.calendars.window.MarketWindow(calendar_key, open_time=, close_time=, tz=,
  pre_open_min=0)`: `is_trading_day`, `now_local`, `open_dt`, `close_dt`,
  `poll_start`, `next_open` (walks at most 30 days).

Known callers: `storage/v2_us.py`, the eodhd and schwab providers, the api `us.py`
routes, `ingest-broker` `snapshot.py` (`next_scheduled_run`) and
`orchestration.cli.in_session` (`MarketWindow`, XBOM for IND, XNYS for USA).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| (none) | - | - | No environment reads and no secrets |

## Data

No ClickHouse tables and no files. `tzdata` is a dependency so zoneinfo keys resolve
in slim images without a system tz database.

## Dependencies and contracts

- Third party: `exchange-calendars>=4.5`, `tzdata>=2024.1`. No workspace imports.
- Contract: all returned datetimes are timezone-aware; `next_scheduled_run` returns
  UTC and raises `ValueError` for a naive `now` or an empty slot list, and
  `RuntimeError` when no session falls inside the horizon.
- Calendar keys are `exchange_calendars` codes (`XNYS`, `XBOM`); India uses the BSE
  calendar (`XBOM`) as its NSE session proxy in orchestration.

## Observability

Nothing is logged here. Callers log through `factorlab.core.logging`; a wrong
session decision shows up as idle cycles in the daemon's logs (read with the
[factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md) skill).

## Tests

`uv run pytest libs/calendars` (XNYS slots, weekends, DST, holidays, argument
validation). `MarketWindow` is also exercised in `libs/runtime/tests/test_runtime.py`
and `in_session` in `tests/test_factlab_ingest_cli.py`.

## Release and rollback

Never released alone; it ships inside the images whose closure includes it
([affected.py](../../tools/affected.py)): `api`, `ingest-broker`, `ingest-india`,
`ingest-political` and `ingest-us`. Roll back by redeploying those components'
previous tags. The library version is not tagged.

## Pitfalls

- `MarketWindow` uses fixed `open_time`/`close_time`, so it does not know early
  closes; `us.bounds()` does (it reads the exchange calendar's session close).
- `factorlab.calendars.us` imports `exchange_calendars` at module import (slow);
  `window.py` defers the import until first use.
- `calendar()` is `lru_cache`d with an end date of the current year + 2 fixed on
  first call; a process that runs for years would need a restart.
- Market hours are duplicated in `storage/sinks/calendar.py` and
  `orchestration.cli.MARKET_WINDOWS`; change them together.
