"""US market explorer and read API, scoped to the USA data products."""
from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from factorlab.sources.schwab.market import NY, bounds, latest_completed
from factorlab.storage.clickhouse import ClickHouseStorage
from factorlab.storage.us_clickhouse import rows


class USPage(BaseModel):
    items: list[dict[str, Any]]
    limit: int
    total: int | None = None
    offset: int = 0
    next_cursor: str | None = None
    data_as_of: datetime | None = None


def selected_day(now):
    today = now.astimezone(NY).date()
    session = bounds(today)
    return today if session and now >= session[0] else latest_completed(now, grace_minutes=0)


class USRepository:
    def __init__(self, client):
        self.client = client

    def query(self, sql, **parameters):
        return rows(self.client.query(sql, parameters=parameters))

    def source_status(self, source="schwab"):
        items = self.query("""SELECT source, status, detail, checked_at
            FROM us_source_status FINAL WHERE source = {source:String}""", source=source)
        if not items:
            return {"source": source, "status": "not_configured",
                    "detail": "Collector has not reported yet", "checked_at": None}
        item = items[0]
        if (datetime.now(UTC) - item["checked_at"]).total_seconds() > 600:
            item.update(status="stale", detail="Collector heartbeat is older than ten minutes")
        return item

    def sources_status(self):
        return [self.source_status("universe"), self.source_status("eodhd"),
                self.source_status("schwab")]

    def overall_status(self):
        sources = self.sources_status()
        priority = {"error": 6, "auth_required": 5, "stale": 4, "incomplete": 3,
                    "waiting": 2, "recovering": 1, "ready": 0}
        return max(sources, key=lambda item: priority.get(item["status"], 2))

    def instruments(self, *, trading_date, search="", scope="all", limit=100, offset=0):
        scope_sql = {
            "all": "",
            "minute": "AND instrument_id IN (SELECT instrument_id FROM us_expected_series FINAL WHERE active AND resolution = '1min')",
            "daily_only": "AND instrument_id IN (SELECT instrument_id FROM us_expected_series FINAL WHERE active AND resolution = 'daily') AND instrument_id NOT IN (SELECT instrument_id FROM us_expected_series FINAL WHERE active AND resolution = '1min')",
            "no_data": "AND instrument_id NOT IN (SELECT instrument_id FROM market_candles_daily FINAL WHERE market_code = 'USA')",
        }.get(scope)
        if scope_sql is None:
            raise HTTPException(422, "Invalid US universe scope")
        where = f"""market_code = 'USA' AND status = 'active'
              AND (positionCaseInsensitiveUTF8(trading_symbol, {{search:String}}) > 0
                   OR positionCaseInsensitiveUTF8(name, {{search:String}}) > 0)
              {scope_sql}"""
        total_rows = self.query(f"SELECT count() AS total FROM ref_instruments FINAL WHERE {where}",
                                search=search)
        total = int(total_rows[0]["total"]) if total_rows else 0
        instruments = self.query(f"""
            SELECT instrument_id, trading_symbol AS symbol, name, exchange_code, currency_code, asset_class
            FROM ref_instruments FINAL WHERE {where}
            ORDER BY trading_symbol LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            """, search=search, limit=limit, offset=offset)
        if not instruments:
            return USPage(items=[], limit=limit, total=total, offset=offset)
        ids = [r["instrument_id"] for r in instruments]
        states = self.query("SELECT * FROM us_recovery_state FINAL WHERE instrument_id IN {ids:Array(UUID)}", ids=ids)
        configured = self.query("""SELECT instrument_id, resolution, source
            FROM us_expected_series FINAL WHERE active AND instrument_id IN {ids:Array(UUID)}""", ids=ids)
        active = {(r["instrument_id"], r["resolution"]): r["source"] for r in configured}
        coverage = self.query("""SELECT * FROM us_session_coverage FINAL
            WHERE trade_date = {day:Date} AND instrument_id IN {ids:Array(UUID)}""", day=trading_date, ids=ids)
        state_map = {(r["instrument_id"], r["resolution"], r["source"]): r for r in states}
        coverage_map = {(r["instrument_id"], r["resolution"], r["source"]): r for r in coverage}
        now = datetime.now(UTC)
        session = bounds(trading_date)
        for item in instruments:
            item["series"] = []
            for resolution in ("1min", "daily"):
                key = (item["instrument_id"], resolution)
                source = active.get(key, "eodhd" if resolution == "daily" else "schwab")
                state = state_map.get((*key, source), {})
                cov = coverage_map.get((*key, source), {})
                expected = 0
                if session:
                    expected = (max(0, int((min(now, session[1]) - session[0]).total_seconds() // 60))
                                if resolution == "1min" else int(now >= session[1] + timedelta(minutes=30)))
                actual = int(cov.get("actual", 0))
                missing = max(0, expected - actual)
                available = state.get("available_from")
                unavailable = available is not None and trading_date < available.astimezone(NY).date()
                status = (("not_selected" if resolution == "1min" else "not_configured")
                          if key not in active else
                          "incomplete" if str(state.get("error") or "").startswith("Coverage incomplete:") else "error" if state.get("error") else
                          "unavailable" if unavailable else "not_expected" if expected == 0 else
                          "recovering" if not state.get("history_complete") else "incomplete" if missing else "complete")
                item["series"].append({"resolution": resolution, "source": source,
                    "status": status, "expected": expected,
                    "actual": actual, "missing": missing, "available_from": available,
                    "last_bar": state.get("last_bar"), "history_complete": state.get("history_complete", False),
                    "error": state.get("error"), "checked_at": state.get("ingested_at")})
        return USPage(items=instruments, limit=limit, total=total, offset=offset,
            data_as_of=max((s["ingested_at"] for s in states), default=None))

    def dashboard(self, trading_date=None):
        now = datetime.now(UTC)
        day = trading_date or selected_day(now)
        today = bounds(now.astimezone(NY).date())
        market_status = "open" if today and today[0] <= now < today[1] else "closed"
        count_rows = self.query("""
            SELECT
              (SELECT count() FROM ref_instruments FINAL WHERE market_code = 'USA' AND status = 'active') AS active,
              (SELECT count() FROM us_expected_series FINAL
                  WHERE active AND source = 'eodhd' AND resolution = 'daily') AS daily_configured,
              (SELECT count() FROM us_expected_series FINAL
                  WHERE active AND source = 'schwab' AND resolution = '1min') AS minute_configured,
              (SELECT uniqExact(instrument_id) FROM market_candles_daily FINAL
                  WHERE market_code = 'USA' AND source = 'eodhd'
                    AND instrument_id IN (SELECT instrument_id FROM us_expected_series FINAL
                        WHERE active AND source = 'eodhd' AND resolution = 'daily')) AS daily_with_data,
              (SELECT uniqExact(instrument_id) FROM market_candles_1min FINAL
                  WHERE market_code = 'USA' AND source = 'schwab'
                    AND instrument_id IN (SELECT instrument_id FROM us_expected_series FINAL
                        WHERE active AND source = 'schwab' AND resolution = '1min')) AS minute_with_data,
              (SELECT max(ingested_at) FROM ref_instruments FINAL WHERE market_code = 'USA' AND status = 'active') AS master_as_of,
              (SELECT max(ingested_at) FROM us_expected_series FINAL WHERE active AND resolution = '1min') AS ranking_as_of
            """)
        counts = count_rows[0] if count_rows else {}
        session = bounds(day)
        totals = {}
        for resolution in ("1min", "daily"):
            configured = int(counts.get(f"{'minute' if resolution == '1min' else 'daily'}_configured", 0))
            per_series = 0
            if session:
                per_series = (max(0, int((min(now, session[1]) - session[0]).total_seconds() // 60))
                              if resolution == "1min" else int(now >= session[1] + timedelta(minutes=30)))
            expected = configured * per_series
            source = "schwab" if resolution == "1min" else "eodhd"
            actual_rows = self.query("""SELECT sum(actual) AS actual FROM us_session_coverage FINAL
                WHERE source = {source:String} AND resolution = {resolution:String}
                  AND trade_date = {day:Date}
                  AND instrument_id IN (SELECT instrument_id FROM us_expected_series FINAL
                      WHERE active AND source = {source:String}
                        AND resolution = {resolution:String})""",
                source=source, resolution=resolution, day=day)
            actual = int(actual_rows[0]["actual"] or 0) if actual_rows else 0
            totals[resolution] = {"expected": expected, "actual": actual,
                                  "missing": max(0, expected - actual),
                                  "coverage_percent": round(min(100, actual * 100 / expected), 2) if expected else None}
        active = int(counts.get("active", 0))
        daily_configured = int(counts.get("daily_configured", 0))
        universe = {**counts, "no_daily_data": max(
            0, daily_configured - int(counts.get("daily_with_data", 0)))}
        sources = self.sources_status()
        return {"generated_at": now, "trading_date": day, "market_status": market_status,
                "instruments": active, "universe": universe, "resolutions": totals,
                "source": self.overall_status(), "sources": sources,
                "price_basis": "Provider OHLCV; no separately supplied adjusted close", "session": "regular"}

    def candles(self, resolution, *, symbol, date_from, date_to, cursor, limit):
        daily = resolution == "daily"
        table = "market_candles_daily" if daily else "market_candles_1min"
        column = "trade_date" if daily else "bar_time"
        expr = "trade_date" if daily else "toDate(bar_time, 'America/New_York')"
        typ = "Date" if daily else "DateTime64(3, 'UTC')"
        parameters = {"symbol": symbol.upper() if symbol else "", "start": date_from, "end": date_to, "limit": limit + 1}
        source = "eodhd" if daily else "schwab"
        parameters["source"] = source
        conditions = ["market_code = 'USA'", "source = {source:String}", f"{expr} BETWEEN {{start:Date}} AND {{end:Date}}"]
        if symbol:
            conditions.append("symbol = {symbol:String}")
        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor))
                if data[0] != [resolution, parameters["symbol"], str(date_from), str(date_to)]:
                    raise ValueError("Cursor scope mismatch")
                parameters.update(cursor_time=date.fromisoformat(data[1]) if daily else datetime.fromisoformat(data[1]),
                                  cursor_symbol=data[2], cursor_id=UUID(data[3]))
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                raise HTTPException(422, "Invalid candle cursor") from exc
            conditions.append(f"({column}, symbol, instrument_id) < ({{cursor_time:{typ}}}, {{cursor_symbol:String}}, {{cursor_id:UUID}})")
        result = self.query(f"""SELECT instrument_id, symbol, market_code, {column}, open, high, low, close,
            volume, {'adj_close' if daily else 'oi'}, source, as_of_time, ingested_at
            FROM {table} FINAL WHERE {' AND '.join(conditions)}
            ORDER BY {column} DESC, symbol DESC, instrument_id DESC LIMIT {{limit:UInt16}}""", **parameters)
        items = result[:limit]
        next_cursor = None
        if len(result) > limit:
            row = items[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps([
                [resolution, parameters["symbol"], str(date_from), str(date_to)], row[column].isoformat(),
                row["symbol"], str(row["instrument_id"])]).encode()).decode()
        return USPage(items=items, limit=limit, next_cursor=next_cursor,
                      data_as_of=max((r["as_of_time"] for r in items), default=None))


def repository():
    """Create a request-local client because clickhouse-connect sessions are not thread-safe."""
    return USRepository(ClickHouseStorage.from_environment().client)


Repo = Annotated[USRepository, Depends(repository)]
router = APIRouter()


@router.get("/dashboard")
def dashboard(repo: Repo, trading_date: date | None = None):
    return repo.dashboard(trading_date)


@router.get("/sources/status")
def source_status(repo: Repo):
    return {"items": repo.sources_status()}


@router.get("/instruments", response_model=USPage)
def instruments(repo: Repo, trading_date: date | None = None,
                search: Annotated[str, Query(max_length=100)] = "",
                scope: Literal["all", "minute", "daily_only", "no_data"] = "all",
                limit: Annotated[int, Query(ge=1, le=250)] = 100,
                offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0):
    return repo.instruments(trading_date=trading_date or selected_day(datetime.now(UTC)),
                            search=search, scope=scope, limit=limit, offset=offset)


@router.get("/candles/{resolution}", response_model=USPage)
def candles(repo: Repo, resolution: Literal["1min", "daily"],
            symbol: Annotated[str | None, Query(max_length=30, pattern=r"^[A-Za-z0-9.-]+$")] = None,
            date_from: date | None = None, date_to: date | None = None,
            cursor: Annotated[str | None, Query(max_length=1000)] = None,
            limit: Annotated[int, Query(ge=1, le=1000)] = 500):
    end = date_to or datetime.now(NY).date()
    start = date_from or end - timedelta(days=30)
    if start > end or start < date(1970, 1, 1) or (resolution == "1min" and (end - start).days > 366):
        raise HTTPException(422, "Invalid date range (minute requests allow at most 366 days)")
    return repo.candles(resolution, symbol=symbol, date_from=start, date_to=end, cursor=cursor, limit=limit)


@router.get("/instruments/{instrument_id}/days", response_model=USPage)
def days(repo: Repo, instrument_id: UUID, date_from: date | None = None, date_to: date | None = None):
    end = date_to or datetime.now(NY).date()
    start = date_from or end - timedelta(days=90)
    if start > end or (end - start).days > 366:
        raise HTTPException(422, "History range must be at most 366 days")
    found = repo.query("""SELECT instrument_id FROM ref_instruments FINAL
        WHERE market_code = 'USA' AND instrument_id = {id:UUID}""", id=instrument_id)
    if not found:
        raise HTTPException(404, "US instrument not found")
    items = repo.query("""SELECT trade_date, resolution, source, expected, actual, missing, ingested_at
        FROM us_session_coverage FINAL WHERE instrument_id = {id:UUID}
        AND trade_date BETWEEN {start:Date} AND {end:Date} ORDER BY trade_date DESC, resolution""",
        id=instrument_id, start=start, end=end)
    return USPage(items=items, limit=734)


@router.get("/ingestion/runs", response_model=USPage)
def runs(repo: Repo, limit: Annotated[int, Query(ge=1, le=250)] = 50,
         offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0):
    return USPage(items=repo.query("""SELECT run_id, pipeline, source, status, started_at, completed_at,
        successful_series, failed_series, rows_written, error, metadata_json FROM ingestion_runs FINAL
        WHERE market_code = 'USA'
        ORDER BY started_at DESC, run_id DESC LIMIT {limit:UInt16} OFFSET {offset:UInt32}""",
        limit=limit, offset=offset), limit=limit)
