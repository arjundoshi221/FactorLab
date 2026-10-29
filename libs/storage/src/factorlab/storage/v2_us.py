"""US live collection against canonical ClickHouse v2 tables."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from factorlab.calendars.us import NY, bounds, calendar
from factorlab.storage.clickhouse import _version, rows
from factorlab.storage.v2_india import V2IndiaStorage

US_EXTRA_EXCHANGES = {
    "NYSE American": ("XASE", "NYSE American"),
    "Cboe BZX": ("BATS", "Cboe BZX Exchange"),
    # Pseudo venue for published index values ($SPX, $COMPX, ...); nothing trades here.
    "US Index": ("INDX", "US index values"),
}
# Futures venues (F-043), coded by MIC: ref.contracts must share the exchange_code of
# the root listing it hangs under, so no name-to-MIC mapping may sit in between.
US_FUTURES_EXCHANGES = {
    "XCME": ("XCME", "Chicago Mercantile Exchange"),
    "XCBT": ("XCBT", "Chicago Board of Trade"),
    "XNYM": ("XNYM", "New York Mercantile Exchange"),
    "XCEC": ("XCEC", "Commodity Exchange"),
    "XCBF": ("XCBF", "Cboe Futures Exchange"),
    "IFUS": ("IFUS", "ICE Futures U.S."),
}
_EQUITY_SESSIONS = {"regular": {"open": "09:30", "close": "16:00"}}
# Bar values a history refresh compares before rewriting a stored day.
DAILY_BAR_VALUES = (
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "trades_count",
    "oi",
    "settlement_price",
)
# The trailing days a refresh always rewrites, so coverage keeps a fresh ingested_at.
REWRITE_DAYS = 7
_GLOBEX_SESSIONS = {"regular": {"open": "18:00", "close": "17:00"}}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class V2USStorage(V2IndiaStorage):
    country_code = "US"

    def seed_us_reference_data(self) -> None:
        """Ensure the approved auxiliary US venues exist without changing schema."""
        for exchange in (*US_EXTRA_EXCHANGES, *US_FUTURES_EXCHANGES):
            self._ensure_us_exchange(exchange)

    def _ensure_us_exchange(self, exchange: str) -> None:
        definition = US_EXTRA_EXCHANGES.get(exchange) or US_FUTURES_EXCHANGES.get(exchange)
        if definition is None:
            return
        exists = self.client.query(
            "SELECT count() FROM ref.exchanges FINAL WHERE exchange_code = {exchange:String}",
            parameters={"exchange": exchange},
        ).result_rows[0][0]
        if exists:
            return
        mic, name = definition
        sessions = _GLOBEX_SESSIONS if exchange in US_FUTURES_EXCHANGES else _EQUITY_SESSIONS
        now = datetime.now(UTC)
        self._insert_dicts(
            "ref.exchanges",
            [
                {
                    "exchange_code": exchange,
                    "mic": mic,
                    "name": name,
                    "country_code": "US",
                    "currency_code": "USD",
                    "timezone": "America/New_York",
                    "sessions": json.dumps(sessions),
                    "active": True,
                    "version": _version(now),
                    "ingested_at": now,
                }
            ],
        )

    def sync_expected_series(self, series, *, source, universe, resolution):
        now = datetime.now(UTC)
        current = {uuid.UUID(str(item["instrument_id"])): item for item in series}
        existing = rows(
            self.client.query(
                "SELECT listing_id, symbol, provider_symbol, universe "
                "FROM meta.expected_series FINAL WHERE country_code = 'US' "
                "AND source = {source:String} AND resolution = {resolution:String} AND active",
                parameters={"source": source, "resolution": resolution},
            )
        )
        records = []
        for listing_id, item in current.items():
            records.append(
                self._expected_record(
                    listing_id,
                    str(item["symbol"]),
                    str(item.get("provider_symbol") or item["symbol"]),
                    source,
                    universe,
                    resolution,
                    True,
                    now,
                )
            )
        for old in existing:
            if old["listing_id"] in current:
                continue
            records.append(
                self._expected_record(
                    old["listing_id"],
                    old["symbol"],
                    old["provider_symbol"],
                    source,
                    old["universe"],
                    resolution,
                    False,
                    now,
                )
            )
        self._insert_dicts("meta.expected_series", records)
        return len(current)

    @staticmethod
    def _expected_record(
        listing_id, symbol, provider_symbol, source, universe, resolution, active, now
    ):
        return {
            "country_code": "US",
            "listing_id": listing_id,
            "contract_id": None,
            "legacy_instrument_id": None,
            "legacy_contract_id": None,
            "source_table": None,
            "symbol": symbol,
            "provider_symbol": provider_symbol,
            "source": source,
            "universe": universe,
            "resolution": resolution,
            "active": active,
            "source_hash": None,
            "version": _version(now),
            "ingested_at": now,
            "migrated_at": None,
        }

    def deactivate_expected_series(self, *, source, resolution):
        current = self.active_expected_series(source=source, resolution=resolution)
        now = datetime.now(UTC)
        self._insert_dicts(
            "meta.expected_series",
            [
                self._expected_record(
                    item["instrument_id"],
                    item["symbol"],
                    item["provider_symbol"],
                    source,
                    item["universe"],
                    resolution,
                    False,
                    now,
                )
                for item in current
            ],
        )
        return len(current)

    def active_expected_series(self, *, source="schwab", resolution="daily"):
        return rows(
            self.client.query(
                "SELECT listing_id AS instrument_id, symbol, provider_symbol, universe, "
                "version, ingested_at FROM meta.expected_series FINAL "
                "WHERE country_code = 'US' AND source = {source:String} "
                "AND resolution = {resolution:String} AND active ORDER BY symbol",
                parameters={"source": source, "resolution": resolution},
            )
        )

    def state(self, instrument_id, resolution, *, source="schwab"):
        result = rows(
            self.client.query(
                "SELECT * FROM meta.recovery_state FINAL "
                "WHERE country_code = 'US' AND listing_id = {id:UUID} "
                "AND source = {source:String} AND resolution = {resolution:String}",
                parameters={"id": instrument_id, "source": source, "resolution": resolution},
            )
        )
        if result:
            return {**result[0], "instrument_id": result[0]["listing_id"]}
        return {
            "instrument_id": instrument_id,
            "source": source,
            "resolution": resolution,
            "history_complete": False,
            "available_from": None,
            "last_bar": None,
            "checked_through": None,
            "full_refreshed_at": None,
            "error": None,
        }

    def states(self, resolution, *, source):
        result = rows(
            self.client.query(
                "SELECT * FROM meta.recovery_state FINAL "
                "WHERE country_code = 'US' AND source = {source:String} "
                "AND resolution = {resolution:String}",
                parameters={"source": source, "resolution": resolution},
            )
        )
        return {
            item["listing_id"]: {**item, "instrument_id": item["listing_id"]} for item in result
        }

    def save_state(self, state):
        now = datetime.now(UTC)
        listing_id = state.get("listing_id") or state["instrument_id"]
        self._insert_dicts(
            "meta.recovery_state",
            [
                {
                    "country_code": "US",
                    "listing_id": listing_id,
                    "legacy_instrument_id": None,
                    "symbol": state.get("symbol") or "",
                    "source": state["source"],
                    "resolution": state["resolution"],
                    "history_complete": state["history_complete"],
                    "available_from": state["available_from"],
                    "last_bar": state["last_bar"],
                    "checked_through": state["checked_through"],
                    "full_refreshed_at": state["full_refreshed_at"],
                    "error": state["error"],
                    "source_hash": None,
                    "version": _version(now),
                    "ingested_at": now,
                    "migrated_at": None,
                }
            ],
        )

    def gap_start(self, instrument_id, resolution, now, *, source="schwab"):
        cutoff = (
            (now - timedelta(days=60)).astimezone(NY).date()
            if resolution == "1min"
            else datetime(1970, 1, 1, tzinfo=UTC).date()
        )
        result = self.client.query(
            "SELECT minOrNull(trade_date) FROM meta.session_coverage FINAL "
            "WHERE country_code = 'US' AND source = {source:String} "
            "AND listing_id = {id:UUID} AND resolution = {resolution:String} "
            "AND missing > 0 AND trade_date >= {cutoff:Date}",
            parameters={
                "source": source,
                "id": instrument_id,
                "resolution": resolution,
                "cutoff": cutoff,
            },
        )
        day = result.result_rows[0][0]
        return datetime(day.year, day.month, day.day, tzinfo=NY).astimezone(UTC) if day else None

    def unresolved_series(self, *, source="schwab"):
        result = self.client.query(
            "SELECT count() FROM meta.recovery_state FINAL "
            "WHERE country_code = 'US' AND source = {source:String} AND error IS NOT NULL",
            parameters={"source": source},
        )
        return int(result.result_rows[0][0])

    def source_status(self, status, detail, *, source="schwab"):
        now = datetime.now(UTC)
        self._insert_dicts(
            "meta.source_status",
            [
                {
                    "country_code": "US",
                    "source": source,
                    "status": status,
                    "detail": detail,
                    "checked_at": now,
                    "version": _version(now),
                }
            ],
        )

    def _coverage_record(
        self,
        listing_id: uuid.UUID,
        symbol: str,
        source: str,
        resolution: str,
        day: date,
        expected: int,
        actual: int,
        now: datetime,
    ) -> dict[str, Any]:
        return {
            "country_code": "US",
            "listing_id": listing_id,
            "legacy_instrument_id": None,
            "symbol": symbol,
            "source": source,
            "resolution": resolution,
            "trade_date": day,
            "expected": expected,
            "actual": actual,
            "missing": max(0, expected - actual),
            "source_hash": None,
            "version": _version(now),
            "ingested_at": now,
            "migrated_at": None,
        }

    def liquid_candidates(self, limit=400):
        return rows(
            self.client.query(
                "WITH recent AS (SELECT listing_id, trade_date, close, volume "
                "FROM market.bars FINAL WHERE country_code = 'US' "
                "AND resolution = 'daily' AND source = 'schwab' "
                # 20 sessions fit in 60 days; the bar_time bound prunes old monthly
                # partitions so FINAL does not scan the full history.
                "AND bar_time >= now() - INTERVAL 60 DAY "
                "AND listing_id IN (SELECT listing_id FROM meta.expected_series FINAL "
                "WHERE country_code = 'US' AND source = 'schwab' "
                "AND resolution = 'daily' AND active) "
                "ORDER BY listing_id, trade_date DESC LIMIT 20 BY listing_id) "
                "SELECT recent.listing_id AS instrument_id, "
                "any(l.trading_symbol) AS symbol, "
                "median(toFloat64(close) * toFloat64(volume)) AS dollar_volume, "
                "count() AS observations FROM recent "
                "INNER JOIN ref.listings AS l FINAL ON l.listing_id = recent.listing_id "
                "GROUP BY recent.listing_id ORDER BY "
                "if(observations >= 15, 1, 0) DESC, dollar_volume DESC, symbol "
                "LIMIT {limit:UInt16} "
                "SETTINGS do_not_merge_across_partitions_select_final = 1",
                parameters={"limit": limit},
            )
        )

    def _insert_daily_by_five_years(self, table: str, records: list[dict[str, Any]]) -> None:
        """Bound each insert to 60 monthly (or, after wave 16, 5 yearly) partitions."""
        windows: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            window = record["trade_date"].year // 5
            windows.setdefault(window, []).append(record)
        for window in sorted(windows):
            self._insert_dicts(table, windows[window])

    def stored_daily_bars(
        self, listing_id: uuid.UUID, *, source: str, start: datetime, end: datetime
    ) -> dict[datetime, tuple[Any, ...]]:
        """Stored daily values keyed by UTC ``bar_time``, for skipping unchanged rewrites."""
        result = self.client.query(
            f"SELECT bar_time, {', '.join(DAILY_BAR_VALUES)} FROM market.bars FINAL "
            "WHERE country_code = 'US' AND listing_id = {id:UUID} AND resolution = 'daily' "
            "AND source = {source:String} AND bar_time >= {start:DateTime64(3, 'UTC')} "
            "AND bar_time <= {end:DateTime64(3, 'UTC')}",
            parameters={"id": listing_id, "source": source, "start": start, "end": end},
        )
        return {_utc(row[0]): tuple(row[1:]) for row in result.result_rows}

    def stored_coverage(
        self, listing_id: uuid.UUID, *, source: str, resolution: str, first: date, last: date
    ) -> dict[date, tuple[int, int]]:
        result = self.client.query(
            "SELECT trade_date, expected, actual FROM meta.session_coverage FINAL "
            "WHERE country_code = 'US' AND source = {source:String} AND listing_id = {id:UUID} "
            "AND resolution = {resolution:String} AND trade_date BETWEEN {first:Date} AND {last:Date}",
            parameters={
                "source": source,
                "id": listing_id,
                "resolution": resolution,
                "first": first,
                "last": last,
            },
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in result.result_rows}

    def coverage(
        self,
        instrument_id: uuid.UUID,
        symbol: str,
        resolution: str,
        start: datetime,
        end: datetime,
        available_from: datetime | None,
        *,
        source: str = "schwab",
    ) -> int | None:
        if available_from is None:
            return None
        first = max(start.astimezone(NY).date(), available_from.astimezone(NY).date())
        last = end.astimezone(NY).date()
        result = rows(
            self.client.query(
                "SELECT trade_date AS day, count() AS actual FROM market.bars FINAL "
                "WHERE country_code = 'US' AND source = {source:String} "
                "AND listing_id = {id:UUID} AND resolution = {resolution:String} "
                "AND trade_date BETWEEN {first:Date} AND {last:Date} GROUP BY day",
                parameters={
                    "source": source,
                    "id": instrument_id,
                    "resolution": resolution,
                    "first": first,
                    "last": last,
                },
            )
        )
        actuals = {item["day"]: item["actual"] for item in result}
        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []
        for session in calendar().sessions_in_range(first, last):
            day = session.date()
            session_bounds = bounds(day)
            if session_bounds is None:
                raise ValueError(f"Calendar session has no bounds: {day}")
            opened, closed = session_bounds
            expected = (
                int(closed <= end)
                if resolution == "daily"
                else max(0, int((min(closed, end) - opened).total_seconds() // 60))
            )
            if expected:
                records.append(
                    self._coverage_record(
                        instrument_id,
                        symbol,
                        source,
                        resolution,
                        day,
                        expected,
                        actuals.get(day, 0),
                        now,
                    )
                )
        missing = sum(item["missing"] for item in records)
        # A full refresh re-derives every day since listing; rewriting the unchanged ones
        # created ~680 monthly parts per listing and OOM-killed ClickHouse.
        fresh_from = last - timedelta(days=REWRITE_DAYS)
        if records and first < fresh_from:
            stored = self.stored_coverage(
                instrument_id, source=source, resolution=resolution, first=first, last=last
            )
            records = [
                item
                for item in records
                if item["trade_date"] >= fresh_from
                or stored.get(item["trade_date"]) != (item["expected"], item["actual"])
            ]
        self._insert_daily_by_five_years("meta.session_coverage", records)
        return missing

    # -- US futures (F-043) ----------------------------------------------------
    def futures_session_closes(self, day: date, *, source: str) -> list[dict[str, Any]]:
        """The last recorded quote of each contract in Globex session ``day``."""
        return rows(
            self.client.query(
                "SELECT contract_id, argMax(source_symbol, observed_at) AS source_symbol, "
                "argMax(day_open, observed_at) AS day_open, "
                "argMax(day_high, observed_at) AS day_high, "
                "argMax(day_low, observed_at) AS day_low, "
                "argMax(last_price, observed_at) AS last_price, "
                "argMax(day_volume, observed_at) AS day_volume, "
                "argMax(oi, observed_at) AS oi, argMax(raw_id, observed_at) AS raw_id, "
                # Not "AS observed_at": the alias would shadow the column inside the
                # argMax calls above (ClickHouse ILLEGAL_AGGREGATION).
                "max(observed_at) AS last_observed_at "
                "FROM market.futures_quotes FINAL "
                "WHERE country_code = 'US' AND source = {source:String} "
                "AND trade_date = {day:Date} GROUP BY contract_id",
                parameters={"source": source, "day": day},
            )
        )

    def futures_settlements(
        self, day: date, *, after: datetime, source: str
    ) -> dict[uuid.UUID, Any]:
        """Each contract's settlement for session ``day``: the first one reported after it.

        Quotes report the latest settlement, so the first non-null value in a later
        session (within the next four days, covering a weekend) is ``day``'s. Quotes
        before ``after`` are ignored: early in a session the vendor may still show the
        settlement before ``day``'s.
        """
        result = rows(
            self.client.query(
                "SELECT contract_id, argMin(settlement_price, observed_at) AS settlement "
                "FROM market.futures_quotes FINAL "
                "WHERE country_code = 'US' AND source = {source:String} "
                "AND trade_date > {day:Date} AND trade_date <= {last:Date} "
                "AND observed_at >= {after:DateTime64(6)} "
                "AND settlement_price IS NOT NULL GROUP BY contract_id",
                parameters={
                    "source": source,
                    "day": day,
                    "last": day + timedelta(days=4),
                    "after": after.astimezone(UTC),
                },
            )
        )
        return {item["contract_id"]: item["settlement"] for item in result}
