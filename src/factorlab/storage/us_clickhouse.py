"""US reference, daily/minute candles, durable recovery, and coverage."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from factorlab.sources.schwab.market import NY, bounds, calendar
from factorlab.storage.clickhouse import (
    _NO_CONTRACT_ID,
    ClickHouseStorage,
    _decimal,
    _integer,
    _version,
    instrument_id_for,
)


def rows(result):
    return [{key: value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None else value
             for key, value in zip(result.column_names, row, strict=True)} for row in result.result_rows]


class USStorage(ClickHouseStorage):
    def insert_dicts(self, table, records):
        if records:
            columns = list(records[0])
            self.client.insert(table, [[r[k] for k in columns] for r in records], column_names=columns)

    def active_reference_count(self):
        result = rows(self.client.query("""SELECT count() AS total FROM ref_instruments FINAL
            WHERE market_code = 'USA' AND status = 'active'"""))
        return int(result[0]["total"])

    def sync_reference_master(self, records, raw_id=None):
        """Replace the active EODHD US common-stock master in batched writes."""
        now = datetime.now(UTC)
        today = now.date()
        existing_rows = rows(self.client.query("""SELECT instrument_id, instrument_key,
            trading_symbol, name, isin, exchange_code, first_seen, last_seen, status
            FROM ref_instruments FINAL WHERE market_code = 'USA'"""))
        existing = {item["instrument_key"]: item for item in existing_rows}
        current = set()
        reference_rows = []
        lookup = {}
        exchanges = {}
        for record in records:
            symbol = str(record["symbol"])
            key = f"USA:{symbol}"
            current.add(key)
            identifier = instrument_id_for(key)
            lookup[symbol] = identifier
            exchange = str(record["exchange_code"])
            exchanges[exchange] = str(record.get("exchange_name") or exchange)
            old = existing.get(key)
            reference_rows.append({
                "instrument_id": identifier, "instrument_key": key,
                "trading_symbol": symbol, "name": str(record.get("name") or symbol),
                "isin": record.get("isin") or None, "exchange_code": exchange,
                "segment": "US_EQ", "instrument_type": "EQ", "asset_class": "equity",
                "country_code": "US", "market_code": "USA", "currency_code": "USD",
                "lot_size": 1, "tick_size": None, "freeze_quantity": None,
                "exchange_token": str(record.get("provider_symbol") or symbol),
                "status": "active", "first_seen": old["first_seen"] if old else today,
                "last_seen": today, "source": "eodhd", "raw_id": raw_id,
                "version": _version(now), "ingested_at": now,
            })
        for key, old in existing.items():
            if key in current or old["status"] == "inactive":
                continue
            reference_rows.append({
                "instrument_id": old["instrument_id"], "instrument_key": key,
                "trading_symbol": old["trading_symbol"], "name": old["name"],
                "isin": old["isin"], "exchange_code": old["exchange_code"],
                "segment": "US_EQ", "instrument_type": "EQ", "asset_class": "equity",
                "country_code": "US", "market_code": "USA", "currency_code": "USD",
                "lot_size": 1, "tick_size": None, "freeze_quantity": None,
                "exchange_token": old["trading_symbol"], "status": "inactive",
                "first_seen": old["first_seen"], "last_seen": old["last_seen"],
                "source": "eodhd", "raw_id": raw_id, "version": _version(now),
                "ingested_at": now,
            })
        self.insert_dicts("ref_countries", [{
            "country_code": "US", "name": "United States", "region": "americas",
            "timezone": str(NY), "source": "eodhd", "version": _version(now),
            "ingested_at": now,
        }])
        self.insert_dicts("ref_exchanges", [{
            "exchange_code": code, "name": name, "country_code": "US",
            "market_code": "USA", "currency_code": "USD", "timezone": str(NY),
            "source": "eodhd", "version": _version(now), "ingested_at": now,
        } for code, name in exchanges.items()])
        self.insert_dicts("ref_instruments", reference_rows)
        return lookup

    def upsert_resolved_constituents(self, constituents, *, source="schwab", raw_id=None):
        """Upsert only resolved members, preserving all unrelated US references."""
        now = datetime.now(UTC)
        today = now.date()
        symbols = [str(item["symbol"] if isinstance(item, dict) else item.symbol)
                   for item in constituents]
        existing_rows = rows(self.client.query("""SELECT instrument_key, first_seen
            FROM ref_instruments FINAL
            WHERE market_code = 'USA' AND trading_symbol IN {symbols:Array(String)}""",
            parameters={"symbols": symbols})) if symbols else []
        first_seen = {item["instrument_key"]: item["first_seen"] for item in existing_rows}
        records = []
        exchanges = {}
        lookup = {}
        for value in constituents:
            item = value if isinstance(value, dict) else value.model_dump()
            symbol = str(item["symbol"])
            key = f"USA:{symbol}"
            identifier = instrument_id_for(key)
            exchange = str(item["exchange"])
            lookup[symbol] = identifier
            exchanges[exchange] = exchange
            records.append({
                "instrument_id": identifier, "instrument_key": key,
                "trading_symbol": symbol, "name": str(item["name"]), "isin": None,
                "exchange_code": exchange, "segment": "US_EQ", "instrument_type": "EQ",
                "asset_class": "equity", "country_code": "US", "market_code": "USA",
                "currency_code": str(item["currency"]), "lot_size": 1, "tick_size": None,
                "freeze_quantity": None, "exchange_token": symbol, "status": "active",
                "first_seen": first_seen.get(key, today), "last_seen": today,
                "source": source, "raw_id": raw_id, "version": _version(now),
                "ingested_at": now,
            })
        self.insert_dicts("ref_countries", [{
            "country_code": "US", "name": "United States", "region": "americas",
            "timezone": str(NY), "source": source, "version": _version(now),
            "ingested_at": now,
        }])
        self.insert_dicts("ref_exchanges", [{
            "exchange_code": code, "name": name, "country_code": "US",
            "market_code": "USA", "currency_code": "USD", "timezone": str(NY),
            "source": source, "version": _version(now), "ingested_at": now,
        } for code, name in exchanges.items()])
        self.insert_dicts("ref_instruments", records)
        return lookup

    def sync_expected_series(self, series, *, source, universe, resolution):
        now = datetime.now(UTC)
        version = _version(now)
        current = {UUID(str(item["instrument_id"])): item for item in series}
        result = rows(self.client.query("""SELECT instrument_id, symbol, provider_symbol, universe
            FROM us_expected_series FINAL
            WHERE source = {source:String} AND resolution = {resolution:String} AND active""",
            parameters={"source": source, "resolution": resolution}))
        inserts = [{
            "instrument_id": identifier, "symbol": str(item["symbol"]),
            "provider_symbol": str(item.get("provider_symbol") or item["symbol"]),
            "source": source, "resolution": resolution, "universe": universe,
            "active": True, "version": version, "ingested_at": now,
        } for identifier, item in current.items()]
        for old in result:
            if old["instrument_id"] not in current:
                inserts.append({**old, "source": source, "resolution": resolution,
                    "active": False, "version": version, "ingested_at": now})
        self.insert_dicts("us_expected_series", inserts)
        return len(current)

    def deactivate_expected_series(self, *, source, resolution):
        """Deactivate active expectations without deleting candles or recovery state."""
        current = rows(self.client.query("""SELECT instrument_id, symbol, provider_symbol, universe
            FROM us_expected_series FINAL
            WHERE source = {source:String} AND resolution = {resolution:String} AND active""",
            parameters={"source": source, "resolution": resolution}))
        if not current:
            return 0
        now = datetime.now(UTC)
        version = _version(now)
        self.insert_dicts("us_expected_series", [{
            **item, "source": source, "resolution": resolution, "active": False,
            "version": version, "ingested_at": now,
        } for item in current])
        return len(current)

    def active_expected_series(self, *, source="schwab", resolution="daily"):
        """Return the currently published collection universe in stable order."""
        return rows(self.client.query("""SELECT instrument_id, symbol, provider_symbol, universe,
            version, ingested_at FROM us_expected_series FINAL
            WHERE source = {source:String} AND resolution = {resolution:String} AND active
            ORDER BY symbol""", parameters={"source": source, "resolution": resolution}))

    def reference(self, symbol, provider_symbol, record, raw_id, universe):
        now = datetime.now(UTC)
        instrument_key = f"schwab:USA:{symbol}"
        instrument_id = instrument_id_for(instrument_key)
        existing = rows(self.client.query(
            "SELECT first_seen FROM ref_instruments FINAL WHERE instrument_key = {key:String}",
            parameters={"key": instrument_key}))
        exchange = record["exchange"]
        self.insert_dicts("ref_countries", [{"country_code": "US", "name": "United States",
            "region": "americas", "timezone": str(NY), "source": "schwab", "version": _version(now), "ingested_at": now}])
        self.insert_dicts("ref_exchanges", [{"exchange_code": exchange, "name": exchange,
            "country_code": "US", "market_code": "USA", "currency_code": "USD", "timezone": str(NY),
            "source": "schwab", "version": _version(now), "ingested_at": now}])
        self.insert_dicts("ref_instruments", [{
            "instrument_id": instrument_id, "instrument_key": instrument_key, "trading_symbol": symbol,
            "name": record.get("description", symbol), "isin": None, "exchange_code": exchange,
            "segment": "US_EQ", "instrument_type": "ETF" if record.get("assetType") == "ETF" else "EQ",
            "asset_class": "etf" if record.get("assetType") == "ETF" else "equity", "country_code": "US",
            "market_code": "USA", "currency_code": "USD", "lot_size": 1, "tick_size": None,
            "freeze_quantity": None, "exchange_token": provider_symbol, "status": "active",
            "first_seen": existing[0]["first_seen"] if existing else now.date(), "last_seen": now.date(),
            "source": "schwab", "raw_id": raw_id, "version": _version(now), "ingested_at": now,
        }])
        self.insert_dicts("us_expected_series", [{"instrument_id": instrument_id, "symbol": symbol,
            "provider_symbol": provider_symbol, "source": "schwab", "resolution": resolution,
            "universe": universe, "active": True, "version": _version(now), "ingested_at": now}
            for resolution in ("1min", "daily")])
        return instrument_id

    def state(self, instrument_id, resolution, *, source="schwab"):
        result = rows(self.client.query("""
            SELECT * FROM us_recovery_state FINAL
            WHERE instrument_id = {id:UUID} AND source = {source:String}
              AND resolution = {resolution:String}
            """, parameters={"id": instrument_id, "source": source, "resolution": resolution}))
        if result:
            return result[0]
        return {"instrument_id": instrument_id, "source": source, "resolution": resolution,
                "history_complete": False, "available_from": None, "last_bar": None,
                "checked_through": None, "full_refreshed_at": None, "error": None}

    def states(self, resolution, *, source):
        result = rows(self.client.query("""SELECT * FROM us_recovery_state FINAL
            WHERE source = {source:String} AND resolution = {resolution:String}""",
            parameters={"source": source, "resolution": resolution}))
        return {item["instrument_id"]: item for item in result}

    def save_state(self, state):
        now = datetime.now(UTC)
        self.insert_dicts("us_recovery_state", [{**state, "version": _version(now), "ingested_at": now}])

    def gap_start(self, instrument_id, resolution, now, *, source="schwab"):
        cutoff = ((now - timedelta(days=60)).astimezone(NY).date()
                  if resolution == "1min" else datetime(1970, 1, 1, tzinfo=UTC).date())
        result = rows(self.client.query("""SELECT minOrNull(trade_date) AS day
            FROM us_session_coverage FINAL WHERE source = {source:String} AND instrument_id = {id:UUID}
              AND resolution = {resolution:String} AND missing > 0 AND trade_date >= {cutoff:Date}
            """, parameters={"id": instrument_id, "source": source,
                               "resolution": resolution, "cutoff": cutoff}))
        day = result[0]["day"]
        return datetime(day.year, day.month, day.day, tzinfo=NY).astimezone(UTC) if day else None

    def unresolved_series(self, *, source="schwab"):
        result = rows(self.client.query("""SELECT count() AS total FROM us_recovery_state FINAL
            WHERE source = {source:String} AND error IS NOT NULL""",
            parameters={"source": source}))
        return result[0]["total"]

    def source_status(self, status, detail, *, source="schwab"):
        now = datetime.now(UTC)
        self.insert_dicts("us_source_status", [{"source": source, "status": status,
            "detail": detail, "checked_at": now, "version": _version(now)}])

    def write_daily(self, frame, *, instrument_id, symbol, raw_id, source="schwab"):
        now = datetime.now(UTC)
        self.insert_dicts("market_candles_daily", [{
            "instrument_id": instrument_id, "contract_id": _NO_CONTRACT_ID, "symbol": symbol,
            "market_code": "USA", "trade_date": row["trade_date"],
            **{k: _decimal(row[k], 6) for k in ("open", "high", "low", "close")},
            "adj_close": _decimal(row.get("adj_close"), 6), "volume": _integer(row["volume"]),
            "source": source, "raw_id": raw_id,
            "as_of_time": now, "ingested_at": now, "version": _version(now),
        } for row in frame.to_dict("records")])
        return len(frame)

    def write_daily_records(self, records, *, source="eodhd"):
        """Write normalized daily rows for many instruments with one insert."""
        now = datetime.now(UTC)
        inserts = [{
            "instrument_id": item["instrument_id"], "contract_id": _NO_CONTRACT_ID,
            "symbol": item["symbol"], "market_code": "USA", "trade_date": item["trade_date"],
            **{key: _decimal(item.get(key), 6) for key in ("open", "high", "low", "close")},
            "adj_close": _decimal(item.get("adj_close"), 6),
            "volume": _integer(item.get("volume")), "source": source,
            "raw_id": item.get("raw_id"), "as_of_time": now, "ingested_at": now,
            "version": _version(now),
        } for item in records]
        self.insert_dicts("market_candles_daily", inserts)
        return len(inserts)

    def record_daily_snapshot_coverage(self, items, records, trading_date, *, source="eodhd"):
        """Record one daily expectation for every active master instrument."""
        now = datetime.now(UTC)
        present = {UUID(str(item["instrument_id"])) for item in records}
        self.insert_dicts("us_session_coverage", [{
            "instrument_id": item["instrument_id"], "symbol": item["symbol"],
            "source": source, "resolution": "daily", "trade_date": trading_date,
            "expected": 1, "actual": int(UUID(str(item["instrument_id"])) in present),
            "missing": int(UUID(str(item["instrument_id"])) not in present),
            "version": _version(now), "ingested_at": now,
        } for item in items])
        return present

    def liquid_candidates(self, limit=400):
        """Rank configured daily stocks by recent median dollar volume."""
        result = rows(self.client.query("""
            WITH recent AS (
                SELECT instrument_id, symbol, trade_date, close, volume
                FROM market_candles_daily FINAL
                WHERE market_code = 'USA' AND source = 'schwab'
                  AND instrument_id IN (
                      SELECT instrument_id FROM us_expected_series FINAL
                      WHERE source = 'schwab' AND resolution = 'daily' AND active)
                ORDER BY instrument_id, trade_date DESC
                LIMIT 20 BY instrument_id
            )
            SELECT instrument_id, argMax(symbol, trade_date) AS symbol,
                   median(toFloat64(close) * toFloat64(volume)) AS dollar_volume,
                   count() AS observations
            FROM recent GROUP BY instrument_id
            ORDER BY if(observations >= 15, 1, 0) DESC, dollar_volume DESC, symbol
            LIMIT {limit:UInt16}
            """, parameters={"limit": limit}))
        return result

    def coverage(self, instrument_id, symbol, resolution, start, end, available_from, *, source="schwab"):
        if available_from is None:
            return
        first = max(start.astimezone(NY).date(), available_from.astimezone(NY).date())
        last = end.astimezone(NY).date()
        table = "market_candles_daily" if resolution == "daily" else "market_candles_1min"
        expr = "trade_date" if resolution == "daily" else "toDate(bar_time, 'America/New_York')"
        counts = rows(self.client.query(f"""
            SELECT {expr} AS day, count() AS actual FROM {table} FINAL
            WHERE market_code = 'USA' AND source = {{source:String}} AND instrument_id = {{id:UUID}}
              AND {expr} BETWEEN {{first:Date}} AND {{last:Date}}
            GROUP BY day
            """, parameters={"id": instrument_id, "source": source,
                               "first": first, "last": last}))
        actuals = {r["day"]: r["actual"] for r in counts}
        now = datetime.now(UTC)
        records = []
        for session in calendar().sessions_in_range(first, last):
            day = session.date()
            opened, closed = bounds(day)
            expected = (int(closed <= end) if resolution == "daily" else
                        max(0, int((min(closed, end) - opened).total_seconds() // 60)))
            if not expected:
                continue
            actual = actuals.get(day, 0)
            records.append({"instrument_id": instrument_id, "symbol": symbol, "source": source,
                "resolution": resolution, "trade_date": day, "expected": expected, "actual": actual,
                "missing": max(0, expected - actual), "version": _version(now), "ingested_at": now})
        self.insert_dicts("us_session_coverage", records)
        return sum(r["missing"] for r in records)
