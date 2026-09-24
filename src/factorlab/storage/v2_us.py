"""US live collection against canonical ClickHouse v2 tables."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any

from factorlab.sources.schwab.market import NY, bounds, calendar
from factorlab.storage.clickhouse import _decimal, _integer, _version
from factorlab.storage.us_clickhouse import rows
from factorlab.storage.v2_india import V2IndiaStorage
from factorlab.storage.v2_reference import UnresolvedReference

US_EXCHANGE_CODES = {
    "XNAS": "NASDAQ", "XNYS": "NYSE", "ARCX": "NYSE Arca",
    "XASE": "NYSE American", "BATS": "Cboe BZX",
    "CBOE": "Cboe BZX", "Z": "Cboe BZX",
}
US_EXTRA_EXCHANGES = {
    "NYSE American": ("XASE", "NYSE American"),
    "Cboe BZX": ("BATS", "Cboe BZX Exchange"),
}


class V2USStorage(V2IndiaStorage):
    country_code = "US"

    def active_reference_count(self) -> int:
        result = self.client.query(
            "SELECT count() FROM ref.listings FINAL "
            "WHERE country_code = 'US' AND active"
        )
        return int(result.result_rows[0][0])

    def _ensure_us_exchange(self, exchange: str) -> None:
        definition = US_EXTRA_EXCHANGES.get(exchange)
        if definition is None:
            return
        exists = self.client.query(
            "SELECT count() FROM ref.exchanges FINAL "
            "WHERE exchange_code = {exchange:String}",
            parameters={"exchange": exchange},
        ).result_rows[0][0]
        if exists:
            return
        mic, name = definition
        now = datetime.now(UTC)
        self._insert_dicts("ref.exchanges", [{
            "exchange_code": exchange, "mic": mic, "name": name,
            "country_code": "US", "currency_code": "USD",
            "timezone": "America/New_York",
            "sessions": json.dumps({"regular": {"open": "09:30", "close": "16:00"}}),
            "active": True, "version": _version(now), "ingested_at": now,
        }])

    def _upsert_us(self, item: Mapping[str, Any], *, source: str,
                   key: str, provider_symbol: str, raw_id: uuid.UUID | None = None) -> uuid.UUID:
        symbol = str(item["symbol"])
        provider_exchange = str(item.get("exchange_code") or item.get("exchange") or "")
        exchange = US_EXCHANGE_CODES.get(provider_exchange, provider_exchange)
        if not exchange:
            raise UnresolvedReference(f"exchange unresolved for {symbol}")
        self._ensure_us_exchange(exchange)
        security_type = "etf" if str(item.get("assetType") or "").upper() == "ETF" else "common"
        try:
            _, _, listing_id = self.references.upsert_listing({
                "instrument_key": key, "isin": item.get("isin"),
                "country_code": "US", "exchange_code": exchange,
                "currency_code": str(item.get("currency") or "USD"),
                "trading_symbol": symbol,
                "name": item.get("name") or item.get("description") or symbol,
                "security_type": security_type, "lot_size": 1,
            }, alias_kind="eodhd_symbol" if source == "eodhd" else "schwab_symbol",
                alias_value=provider_symbol, source=source)
        except UnresolvedReference as exc:
            self._identity_status(source=source, alias_kind="eodhd_symbol" if source == "eodhd"
                                  else "schwab_symbol", alias_value=provider_symbol,
                                  raw_id=raw_id, reason=str(exc))
            raise
        self._identity_status(source=source, alias_kind="eodhd_symbol" if source == "eodhd"
                              else "schwab_symbol", alias_value=provider_symbol, raw_id=raw_id,
                              target_kind="listing", target_id=listing_id)
        return listing_id

    def sync_reference_master(self, records: Sequence[Mapping[str, Any]], raw_id=None):
        lookup = {}
        for item in records:
            symbol = str(item["symbol"])
            lookup[symbol] = self._upsert_us(
                item, source="eodhd", key=f"USA:{symbol}",
                provider_symbol=str(item.get("provider_symbol") or symbol), raw_id=raw_id,
            )
        return lookup

    def upsert_resolved_constituents(self, constituents, *, source="schwab", raw_id=None):
        lookup = {}
        for value in constituents:
            item = value if isinstance(value, dict) else value.model_dump()
            symbol = str(item["symbol"])
            lookup[symbol] = self._upsert_us(
                item, source=source, key=f"USA:{symbol}",
                provider_symbol=symbol, raw_id=raw_id,
            )
        return lookup

    def reference(self, symbol, provider_symbol, record, raw_id, universe):
        return self._upsert_us(
            {**record, "symbol": symbol}, source="schwab",
            key=f"schwab:USA:{symbol}", provider_symbol=provider_symbol,
            raw_id=raw_id,
        )

    def sync_expected_series(self, series, *, source, universe, resolution):
        now = datetime.now(UTC)
        current = {uuid.UUID(str(item["instrument_id"])): item for item in series}
        existing = rows(self.client.query(
            "SELECT listing_id, symbol, provider_symbol, universe "
            "FROM meta.expected_series FINAL WHERE country_code = 'US' "
            "AND source = {source:String} AND resolution = {resolution:String} AND active",
            parameters={"source": source, "resolution": resolution},
        ))
        records = []
        for listing_id, item in current.items():
            records.append(self._expected_record(
                listing_id, str(item["symbol"]),
                str(item.get("provider_symbol") or item["symbol"]),
                source, universe, resolution, True, now,
            ))
        for old in existing:
            if old["listing_id"] in current:
                continue
            records.append(self._expected_record(
                old["listing_id"], old["symbol"], old["provider_symbol"],
                source, old["universe"], resolution, False, now,
            ))
        self._insert_dicts("meta.expected_series", records)
        return len(current)

    @staticmethod
    def _expected_record(listing_id, symbol, provider_symbol, source, universe,
                         resolution, active, now):
        return {
            "country_code": "US", "listing_id": listing_id, "contract_id": None,
            "legacy_instrument_id": None, "legacy_contract_id": None,
            "source_table": None, "symbol": symbol,
            "provider_symbol": provider_symbol, "source": source,
            "universe": universe, "resolution": resolution, "active": active,
            "source_hash": None, "version": _version(now),
            "ingested_at": now, "migrated_at": None,
        }

    def deactivate_expected_series(self, *, source, resolution):
        current = self.active_expected_series(source=source, resolution=resolution)
        now = datetime.now(UTC)
        self._insert_dicts("meta.expected_series", [self._expected_record(
            item["instrument_id"], item["symbol"], item["provider_symbol"],
            source, item["universe"], resolution, False, now,
        ) for item in current])
        return len(current)

    def active_expected_series(self, *, source="schwab", resolution="daily"):
        return rows(self.client.query(
            "SELECT listing_id AS instrument_id, symbol, provider_symbol, universe, "
            "version, ingested_at FROM meta.expected_series FINAL "
            "WHERE country_code = 'US' AND source = {source:String} "
            "AND resolution = {resolution:String} AND active ORDER BY symbol",
            parameters={"source": source, "resolution": resolution},
        ))

    def state(self, instrument_id, resolution, *, source="schwab"):
        result = rows(self.client.query(
            "SELECT * FROM meta.recovery_state FINAL "
            "WHERE country_code = 'US' AND listing_id = {id:UUID} "
            "AND source = {source:String} AND resolution = {resolution:String}",
            parameters={"id": instrument_id, "source": source, "resolution": resolution},
        ))
        if result:
            return {**result[0], "instrument_id": result[0]["listing_id"]}
        return {
            "instrument_id": instrument_id, "source": source,
            "resolution": resolution, "history_complete": False,
            "available_from": None, "last_bar": None, "checked_through": None,
            "full_refreshed_at": None, "error": None,
        }

    def states(self, resolution, *, source):
        result = rows(self.client.query(
            "SELECT * FROM meta.recovery_state FINAL "
            "WHERE country_code = 'US' AND source = {source:String} "
            "AND resolution = {resolution:String}",
            parameters={"source": source, "resolution": resolution},
        ))
        return {item["listing_id"]: {**item, "instrument_id": item["listing_id"]}
                for item in result}

    def save_state(self, state):
        now = datetime.now(UTC)
        listing_id = state.get("listing_id") or state["instrument_id"]
        self._insert_dicts("meta.recovery_state", [{
            "country_code": "US", "listing_id": listing_id,
            "legacy_instrument_id": None, "symbol": state.get("symbol") or "",
            "source": state["source"], "resolution": state["resolution"],
            "history_complete": state["history_complete"],
            "available_from": state["available_from"], "last_bar": state["last_bar"],
            "checked_through": state["checked_through"],
            "full_refreshed_at": state["full_refreshed_at"],
            "error": state["error"], "source_hash": None,
            "version": _version(now), "ingested_at": now, "migrated_at": None,
        }])

    def gap_start(self, instrument_id, resolution, now, *, source="schwab"):
        cutoff = ((now - timedelta(days=60)).astimezone(NY).date()
                  if resolution == "1min" else datetime(1970, 1, 1, tzinfo=UTC).date())
        result = self.client.query(
            "SELECT minOrNull(trade_date) FROM meta.session_coverage FINAL "
            "WHERE country_code = 'US' AND source = {source:String} "
            "AND listing_id = {id:UUID} AND resolution = {resolution:String} "
            "AND missing > 0 AND trade_date >= {cutoff:Date}",
            parameters={"source": source, "id": instrument_id,
                        "resolution": resolution, "cutoff": cutoff},
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
        self._insert_dicts("meta.source_status", [{
            "country_code": "US", "source": source, "status": status,
            "detail": detail, "checked_at": now, "version": _version(now),
        }])

    def _listing_reference(self, listing_id):
        result = self.client.query(
            "SELECT l.security_id, s.entity_id, s.security_type "
            "FROM ref.listings AS l FINAL INNER JOIN ref.securities AS s FINAL "
            "ON s.security_id = l.security_id "
            "WHERE l.listing_id = {id:UUID} AND l.country_code = 'US'",
            parameters={"id": listing_id},
        )
        if len(result.result_rows) != 1:
            raise UnresolvedReference(f"US listing/security unresolved: {listing_id}")
        return result.result_rows[0]

    def _bar_record(self, *, listing_id, trade_date, bar_time, resolution,
                    source, raw_id, prices, volume, now):
        if self._active_run_id is None:
            raise RuntimeError("start an ingestion run before writing v2 bars")
        security_id, entity_id, security_type = self._listing_reference(listing_id)
        session = ("regular" if resolution == "daily" or
                   time(9, 30) <= bar_time.astimezone(NY).time()
                   < time(16, 0) else
                   "pre" if bar_time.astimezone(NY).time() < time(9, 30)
                   else "post")
        return {
            "country_code": "US", "listing_id": listing_id,
            "security_id": security_id, "entity_id": entity_id,
            "product_type": "common" if security_type == "equity" else security_type,
            "resolution": resolution, "session": session,
            "bar_time": bar_time, "trade_date": trade_date,
            **{key: _decimal(prices.get(key), 6)
               for key in ("open", "high", "low", "close")},
            "volume": _integer(volume), "turnover": None, "trades_count": None,
            "oi": None, "settlement_price": None, "source": source,
            "source_channel": source, "raw_id": raw_id,
            "ingest_run_id": self._active_run_id, "as_of_time": now,
            "ingested_at": now, "latency_ms": None, "version": _version(now),
        }

    def write_daily(self, frame, *, instrument_id, symbol, raw_id, source="schwab"):
        now = datetime.now(UTC)
        records = []
        for row in frame.to_dict("records"):
            day = row["trade_date"]
            bar_time = datetime(day.year, day.month, day.day, tzinfo=NY).astimezone(UTC)
            records.append(self._bar_record(
                listing_id=instrument_id, trade_date=day, bar_time=bar_time,
                resolution="daily", source=source, raw_id=raw_id,
                prices=row, volume=row.get("volume"), now=now,
            ))
        self._insert_dicts("market.bars", records)
        return len(records)

    def write_daily_records(self, records, *, source="eodhd"):
        now = datetime.now(UTC)
        inserts = []
        for item in records:
            day = item["trade_date"]
            bar_time = datetime(day.year, day.month, day.day, tzinfo=NY).astimezone(UTC)
            inserts.append(self._bar_record(
                listing_id=item["instrument_id"], trade_date=day,
                bar_time=bar_time, resolution="daily", source=source,
                raw_id=item.get("raw_id"), prices=item,
                volume=item.get("volume"), now=now,
            ))
        self._insert_dicts("market.bars", inserts)
        return len(inserts)

    def write_candles_1min(self, candles, *, instrument_id, symbol,
                           contract_id=None, source="schwab", raw_id=None,
                           market_code="USA"):
        if contract_id is not None:
            raise ValueError("US contract bars require a contract-specific v2 writer")
        now = datetime.now(UTC)
        inserts = []
        for row in candles.to_dict("records"):
            bar_time = row["timestamp"]
            if hasattr(bar_time, "to_pydatetime"):
                bar_time = bar_time.to_pydatetime()
            bar_time = bar_time.astimezone(UTC)
            inserts.append(self._bar_record(
                listing_id=instrument_id, trade_date=bar_time.astimezone(NY).date(),
                bar_time=bar_time, resolution="1min", source=source,
                raw_id=raw_id, prices=row, volume=row.get("volume"), now=now,
            ))
        self._insert_dicts("market.bars", inserts)
        return len(inserts)

    def _coverage_record(self, listing_id, symbol, source, resolution,
                         day, expected, actual, now):
        return {
            "country_code": "US", "listing_id": listing_id,
            "legacy_instrument_id": None, "symbol": symbol,
            "source": source, "resolution": resolution, "trade_date": day,
            "expected": expected, "actual": actual,
            "missing": max(0, expected - actual), "source_hash": None,
            "version": _version(now), "ingested_at": now, "migrated_at": None,
        }

    def record_daily_snapshot_coverage(self, items, records, trading_date, *, source="eodhd"):
        present = {uuid.UUID(str(item["instrument_id"])) for item in records}
        now = datetime.now(UTC)
        self._insert_dicts("meta.session_coverage", [self._coverage_record(
            item["instrument_id"], item["symbol"], source, "daily",
            trading_date, 1, int(item["instrument_id"] in present), now,
        ) for item in items])
        return present

    def liquid_candidates(self, limit=400):
        return rows(self.client.query(
            "WITH recent AS (SELECT listing_id, trade_date, close, volume "
            "FROM market.bars FINAL WHERE country_code = 'US' "
            "AND resolution = 'daily' AND source = 'schwab' "
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
            "LIMIT {limit:UInt16}", parameters={"limit": limit},
        ))

    def coverage(self, instrument_id, symbol, resolution, start, end,
                 available_from, *, source="schwab"):
        if available_from is None:
            return None
        first = max(start.astimezone(NY).date(), available_from.astimezone(NY).date())
        last = end.astimezone(NY).date()
        result = rows(self.client.query(
            "SELECT trade_date AS day, count() AS actual FROM market.bars FINAL "
            "WHERE country_code = 'US' AND source = {source:String} "
            "AND listing_id = {id:UUID} AND resolution = {resolution:String} "
            "AND trade_date BETWEEN {first:Date} AND {last:Date} GROUP BY day",
            parameters={"source": source, "id": instrument_id,
                        "resolution": resolution, "first": first, "last": last},
        ))
        actuals = {item["day"]: item["actual"] for item in result}
        now = datetime.now(UTC)
        records = []
        for session in calendar().sessions_in_range(first, last):
            day = session.date()
            opened, closed = bounds(day)
            expected = (int(closed <= end) if resolution == "daily" else
                        max(0, int((min(closed, end) - opened).total_seconds() // 60)))
            if expected:
                records.append(self._coverage_record(
                    instrument_id, symbol, source, resolution,
                    day, expected, actuals.get(day, 0), now,
                ))
        self._insert_dicts("meta.session_coverage", records)
        return sum(item["missing"] for item in records)
