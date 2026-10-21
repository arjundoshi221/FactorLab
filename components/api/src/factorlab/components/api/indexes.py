"""US indexes and sector performance (ADR 0017), mounted under ``/hub/api/v1/us``.

Two kinds of series share one shape (``trade_date``, ``value``):

* **constructed** FactorLab indexes from ``derived.index_levels`` (``us_gics_45_cw``);
* **tracked** official indexes and ETFs (``$SPX``, ``XLK``): Schwab daily closes from
  ``market.bars``, resolved through their ``schwab_symbol`` alias.

The catalogue (names, sectors, benchmark ETFs) is ``factorlab.indexes``' packaged
``catalog_us.yaml``, the same file the index runner computes from.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from factorlab.calendars.us import NY
from factorlab.clickhouse import ClickHouse, rows
from factorlab.indexes import IndexCatalog, load_catalog, sector_code

DAILY_SOURCE = "schwab"
PERIODS: tuple[tuple[str, Any], ...] = (
    ("1d", None),
    ("1w", timedelta(days=7)),
    ("1m", timedelta(days=30)),
    ("3m", timedelta(days=91)),
    ("ytd", "ytd"),
    ("1y", timedelta(days=365)),
)
_CODE = r"^\$?[A-Za-z0-9_]{1,40}$"


@lru_cache
def catalog() -> IndexCatalog:
    return load_catalog()


class SeriesPoint(BaseModel):
    trade_date: date
    value: float
    complete: bool = True


class Series(BaseModel):
    code: str
    name: str
    kind: Literal["constructed", "tracked"]
    points: list[SeriesPoint]


class SeriesResponse(BaseModel):
    items: list[Series]


def _names() -> dict[str, tuple[str, Literal["constructed", "tracked"]]]:
    cat = catalog()
    names: dict[str, tuple[str, Literal["constructed", "tracked"]]] = {
        d.code: (d.name, "constructed") for d in cat.definitions()
    }
    names.update({t.symbol: (t.name, "tracked") for t in cat.tracked})
    for sector in cat.sectors:
        names[sector.cw_etf] = (f"{sector.name} sector ETF (cap weight)", "tracked")
        names[sector.ew_etf] = (f"{sector.name} sector ETF (equal weight)", "tracked")
    return names


def period_return(points: Sequence[tuple[date, float]], as_of: date, period: Any) -> float | None:
    """Return over ``period`` ending at the last point on or before ``as_of``."""
    upto = [(d, v) for d, v in points if d <= as_of]
    if len(upto) < 2:
        return None
    end_day, end = upto[-1]
    if period is None:
        start = upto[-2][1]
    else:
        anchor = date(end_day.year - 1, 12, 31) if period == "ytd" else end_day - period
        earlier = [v for d, v in upto if d <= anchor]
        if not earlier:
            return None
        start = earlier[-1]
    return end / start - 1.0 if start else None


class IndexRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def query(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:
        return rows(self.client.query(sql, parameters=parameters))

    def constructed(
        self, codes: Sequence[str], start: date, end: date
    ) -> dict[str, list[SeriesPoint]]:
        out: dict[str, list[SeriesPoint]] = {code: [] for code in codes}
        if not codes:
            return out
        for row in self.query(
            """SELECT index_code, trade_date, level, complete
            FROM derived.index_levels FINAL
            WHERE index_code IN {codes:Array(String)}
              AND trade_date BETWEEN {start:Date} AND {end:Date}
            ORDER BY index_code, trade_date""",
            codes=list(codes),
            start=start,
            end=end,
        ):
            out[row["index_code"]].append(
                SeriesPoint(
                    trade_date=row["trade_date"],
                    value=float(row["level"]),
                    complete=bool(row["complete"]),
                )
            )
        return out

    def tracked(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, list[SeriesPoint]]:
        out: dict[str, list[SeriesPoint]] = {symbol: [] for symbol in symbols}
        if not symbols:
            return out
        for row in self.query(
            """SELECT a.alias_value AS symbol, b.trade_date AS trade_date,
                toFloat64(b.close) AS close
            FROM market.bars AS b FINAL
            INNER JOIN (
                SELECT alias_value, target_id FROM ref.identifier_aliases FINAL
                WHERE alias_kind = 'schwab_symbol' AND target_kind = 'listing'
                  AND valid_to IS NULL AND alias_value IN {symbols:Array(String)}
            ) AS a ON a.target_id = b.listing_id
            WHERE b.country_code = 'US' AND b.resolution = 'daily'
              AND b.source = {source:String}
              AND b.trade_date BETWEEN {start:Date} AND {end:Date}
            ORDER BY symbol, trade_date""",
            symbols=list(symbols),
            source=DAILY_SOURCE,
            start=start,
            end=end,
        ):
            out[row["symbol"]].append(
                SeriesPoint(trade_date=row["trade_date"], value=float(row["close"]))
            )
        return out

    def series(self, codes: Sequence[str], start: date, end: date) -> list[Series]:
        names = _names()
        unknown = sorted(set(codes) - names.keys())
        if unknown:
            raise HTTPException(404, f"Unknown index codes: {', '.join(unknown)}")
        constructed = self.constructed(
            [c for c in codes if names[c][1] == "constructed"], start, end
        )
        tracked = self.tracked([c for c in codes if names[c][1] == "tracked"], start, end)
        points = {**constructed, **tracked}
        return [Series(code=c, name=names[c][0], kind=names[c][1], points=points[c]) for c in codes]

    def constituents(self, code: str, day: date | None, limit: int) -> dict[str, Any]:
        if code not in {d.code for d in catalog().definitions()}:
            raise HTTPException(404, f"Unknown constructed index: {code}")
        latest = self.query(
            """SELECT max(trade_date) AS trade_date FROM derived.index_weights FINAL
            WHERE index_code = {code:String} AND trade_date <= {day:Date}""",
            code=code,
            day=day or datetime.now(NY).date(),
        )
        trade_date = latest[0]["trade_date"] if latest else None
        if not trade_date or trade_date == date(1970, 1, 1):
            return {"code": code, "trade_date": None, "items": [], "total": 0}
        items = self.query(
            """SELECT w.listing_id AS listing_id, l.trading_symbol AS symbol,
                e.legal_name AS name, w.weight AS weight, w.ret_1d AS ret_1d,
                w.contribution AS contribution
            FROM derived.index_weights AS w FINAL
            LEFT JOIN ref.listings AS l FINAL ON l.listing_id = w.listing_id
            LEFT JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
            LEFT JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
            WHERE w.index_code = {code:String} AND w.trade_date = {trade_date:Date}
            ORDER BY w.weight DESC
            LIMIT {limit:UInt32}""",
            code=code,
            trade_date=trade_date,
            limit=limit,
        )
        total = self.query(
            """SELECT count() AS n FROM derived.index_weights FINAL
            WHERE index_code = {code:String} AND trade_date = {trade_date:Date}""",
            code=code,
            trade_date=trade_date,
        )
        return {
            "code": code,
            "trade_date": trade_date,
            "items": items,
            "total": int(total[0]["n"]) if total else len(items),
        }

    def movers(self, code: str, trade_date: date, n: int = 5) -> dict[str, list[dict[str, Any]]]:
        rows_ = self.query(
            """SELECT l.trading_symbol AS symbol, w.ret_1d AS ret_1d,
                w.contribution AS contribution
            FROM derived.index_weights AS w FINAL
            LEFT JOIN ref.listings AS l FINAL ON l.listing_id = w.listing_id
            WHERE w.index_code = {code:String} AND w.trade_date = {trade_date:Date}
              AND w.ret_1d IS NOT NULL
            ORDER BY w.contribution DESC""",
            code=code,
            trade_date=trade_date,
        )
        return {"top": rows_[:n], "bottom": list(reversed(rows_[-n:])) if rows_ else []}


def _returns(points: Sequence[SeriesPoint], as_of: date) -> dict[str, float | None]:
    pairs = [(p.trade_date, p.value) for p in points]
    return {name: period_return(pairs, as_of, period) for name, period in PERIODS}


def sector_performance(
    repo: IndexRepository, as_of: date, weighting: Literal["ew", "cw"]
) -> dict[str, Any]:
    cat = catalog()
    start = date(as_of.year - 1, 1, 1) - timedelta(days=10)
    start = min(start, as_of - timedelta(days=375))
    codes = [sector_code(s.sector_id, w) for s in cat.sectors for w in ("ew", "cw")]
    composites = [f"{cat.universe}_ew", f"{cat.universe}_cw"]
    etfs = [s.cw_etf for s in cat.sectors] + [s.ew_etf for s in cat.sectors]
    constructed = repo.constructed(codes + composites, start, as_of)
    tracked = repo.tracked([*etfs, "$SPX", "RSP"], start, as_of)
    latest = max((p.trade_date for code in codes for p in constructed[code]), default=None)
    sectors = []
    for sector in cat.sectors:
        main = constructed[sector_code(sector.sector_id, weighting)]
        entry: dict[str, Any] = {
            "sector_id": sector.sector_id,
            "name": sector.name,
            "code": sector_code(sector.sector_id, weighting),
            "level": main[-1].value if main else None,
            "complete": main[-1].complete if main else False,
            "returns": {
                "ew": _returns(constructed[sector_code(sector.sector_id, "ew")], as_of),
                "cw": _returns(constructed[sector_code(sector.sector_id, "cw")], as_of),
                "cw_etf": _returns(tracked[sector.cw_etf], as_of),
                "ew_etf": _returns(tracked[sector.ew_etf], as_of),
            },
            "cw_etf": sector.cw_etf,
            "ew_etf": sector.ew_etf,
            "movers": {"top": [], "bottom": []},
        }
        if latest:
            entry["movers"] = repo.movers(entry["code"], latest)
        sectors.append(entry)
    return {
        "as_of": latest,
        "weighting": weighting,
        "periods": [name for name, _ in PERIODS],
        "sectors": sectors,
        "benchmarks": {
            "composite_ew": _returns(constructed[composites[0]], as_of),
            "composite_cw": _returns(constructed[composites[1]], as_of),
            "$SPX": _returns(tracked["$SPX"], as_of),
            "RSP": _returns(tracked["RSP"], as_of),
        },
    }


def overview(repo: IndexRepository, as_of: date) -> dict[str, Any]:
    cat = catalog()
    start = as_of - timedelta(days=14)
    tracked = repo.tracked([t.symbol for t in cat.tracked], start, as_of)
    definitions = cat.definitions()
    constructed = repo.constructed([d.code for d in definitions], start, as_of)

    def latest(points: Sequence[SeriesPoint]) -> dict[str, Any]:
        if not points:
            return {"value": None, "change": None, "trade_date": None, "complete": False}
        last = points[-1]
        prev = points[-2].value if len(points) > 1 else None
        return {
            "value": last.value,
            "change": last.value / prev - 1.0 if prev else None,
            "trade_date": last.trade_date,
            "complete": last.complete,
        }

    return {
        "tracked": [
            {"code": t.symbol, "name": t.name, "group": t.group, **latest(tracked[t.symbol])}
            for t in cat.tracked
        ],
        "constructed": [
            {
                "code": d.code,
                "name": d.name,
                "weighting": d.weighting,
                "sector_id": d.sector_id,
                **latest(constructed[d.code]),
            }
            for d in definitions
        ],
        "sectors": [s.model_dump() for s in cat.sectors],
        "base_date": cat.base_date,
        "universe": cat.universe,
    }


def repository() -> IndexRepository:
    """Request-local client: clickhouse-connect sessions are not thread-safe."""
    return IndexRepository(ClickHouse.from_environment().client)


Repo = Annotated[IndexRepository, Depends(repository)]
router = APIRouter()


def _today() -> date:
    return datetime.now(UTC).astimezone(NY).date()


@router.get("/indexes")
def indexes(repo: Repo, as_of: date | None = None) -> dict[str, Any]:
    return overview(repo, as_of or _today())


@router.get("/indexes/series", response_model=SeriesResponse)
def series(
    repo: Repo,
    codes: Annotated[list[str], Query(min_length=1, max_length=12)],
    date_from: date | None = None,
    date_to: date | None = None,
) -> SeriesResponse:
    end = date_to or _today()
    start = date_from or end - timedelta(days=365)
    if start > end or (end - start).days > 366 * 15:
        raise HTTPException(422, "Invalid date range (at most 15 years)")
    for code in codes:
        if not _valid_code(code):
            raise HTTPException(422, f"Invalid index code: {code!r}")
    return SeriesResponse(items=repo.series(list(dict.fromkeys(codes)), start, end))


@router.get("/indexes/{code}/constituents")
def constituents(
    repo: Repo,
    code: str,
    trade_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=3000)] = 100,
) -> dict[str, Any]:
    if not _valid_code(code):
        raise HTTPException(422, f"Invalid index code: {code!r}")
    return repo.constituents(code, trade_date, limit)


@router.get("/sectors/performance")
def sectors_performance(
    repo: Repo, as_of: date | None = None, weighting: Literal["ew", "cw"] = "cw"
) -> dict[str, Any]:
    return sector_performance(repo, as_of or _today(), weighting)


def _valid_code(code: str) -> bool:
    return re.fullmatch(_CODE, code) is not None


__all__ = ["IndexRepository", "period_return", "router", "sector_performance"]
