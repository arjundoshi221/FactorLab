"""Ticker-by-dataset matrix: which FactorLab tickers have which data, and how fresh it is.

Every column group is one set-based query over the page's listing IDs, so the query count
does not grow with the page size. A failed column group reads as ``unavailable`` while the
other columns still render; nothing missing is ever shown as zero or as healthy.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from factorlab.clickhouse import ClickHouse
from factorlab.components.api.catalog import _calendar
from factorlab.components.api.catalog_query import CatalogError
from factorlab.components.api.instruments import Instrument, InstrumentRepository, inventory_sql
from factorlab.components.api.source_health import broker_snapshot_stale

CellState = Literal[
    "ok", "stale", "missing", "stored", "none", "not_configured", "needs_tags", "unavailable"
]
Group = Literal["market", "alternative", "fundamentals", "broker", "tags"]
Level = Literal["listing", "underlying", "issuer", "account", "order"]
Scope = Literal["relevant", "configured", "traded", "held", "all"]
Attention = Literal["", "bars", "untagged"]

EXCHANGES = {"US": ("XNYS", ZoneInfo("America/New_York")), "IN": ("XBOM", ZoneInfo("Asia/Kolkata"))}
# Daily bars land after the close and minute bars trail the session; allow both to settle.
SESSION_GRACE = timedelta(hours=2)
CACHE_SECONDS = 60.0
MAX_FOCUS_IDS = 5000
HELD_LOOKBACK = timedelta(days=14)
# Runs whose manifests list position snapshots: Gateway, simulation and Flex statements.
SNAPSHOT_PIPELINES = (
    "('ibkr_broker_snapshot', 'ibkr_broker_simulation', 'broker.flex_activity:ibkr')"
)


class MatrixColumn(BaseModel):
    key: str
    label: str
    group: Group
    dataset: str
    level: Level
    country: str | None = None
    source: str | None = None
    resolution: str | None = None
    has_intent: bool = False


class MatrixCell(BaseModel):
    state: CellState
    latest: date | datetime | None = None
    count: int | None = None
    detail: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)


class MatrixRow(BaseModel):
    instrument: Instrument
    cells: dict[str, MatrixCell]


class MatrixPage(BaseModel):
    columns: list[MatrixColumn]
    items: list[MatrixRow]
    total: int
    limit: int
    offset: int
    generated_at: datetime
    expected_sessions: dict[str, date]
    unavailable: list[str]


FIXED_COLUMNS = [
    MatrixColumn(
        key="futures",
        label="Futures bars",
        group="market",
        dataset="market.futures_contract_bars",
        level="underlying",
        has_intent=True,
    ),
    MatrixColumn(
        key="fundamentals",
        label="Fundamentals",
        group="fundamentals",
        dataset="fundamentals.filings",
        level="issuer",
    ),
    MatrixColumn(
        key="political",
        label="Political trades",
        group="alternative",
        dataset="alt.political_trades",
        level="listing",
    ),
    MatrixColumn(
        key="positions",
        label="Holdings",
        group="broker",
        dataset="broker.positions_snapshot",
        level="account",
    ),
    MatrixColumn(
        key="trades", label="Trades", group="broker", dataset="broker.executions", level="order"
    ),
    MatrixColumn(
        key="tags",
        label="Tags",
        group="tags",
        dataset="book.order_assignment_events",
        level="order",
    ),
]


# sessions and caching -----------------------------------------------------------------


def expected_session(country: str, now: datetime) -> date | None:
    """The latest session of ``country``'s exchange that closed at least ``SESSION_GRACE`` ago."""
    exchange = EXCHANGES.get(country)
    if exchange is None:
        return None
    import exchange_calendars as xcals
    import pandas as pd

    calendar = _calendar(exchange[0], xcals)
    today = now.astimezone(exchange[1]).date()
    try:
        sessions = calendar.sessions_in_range(
            pd.Timestamp(today - timedelta(days=20)), pd.Timestamp(today)
        )
    except (ValueError, KeyError):
        return None
    for session in reversed(sessions):
        if calendar.session_close(session).to_pydatetime() + SESSION_GRACE <= now:
            return session.date()
    return None


class TTL:
    def __init__(self, seconds: float = CACHE_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._items: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any, build: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        value = build()
        with self._lock:
            self._items[key] = (time.monotonic() + self.seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


CACHE = TTL()


def ids(values: Any) -> list[str]:
    return sorted({str(value) for value in values if value is not None})


def classify(latest: date | None, expected: date | None, configured: bool) -> CellState:
    if latest is None:
        return "missing" if configured else "not_configured"
    if not configured:
        return "stored"
    if expected is None or latest >= expected:
        return "ok"
    return "stale"


def as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


# repository -------------------------------------------------------------------------------


class MatrixRepository:
    def __init__(self, client: Any, now: datetime | None = None) -> None:
        self.repo = InstrumentRepository(client)
        self.now = now or datetime.now(UTC)

    def query(
        self, sql: str, params: dict[str, Any], max_rows: int = 20_000
    ) -> list[dict[str, Any]]:
        return self.repo.query(sql, params, max_rows=max_rows)

    # configuration and freshness, shared by the matrix and the monitoring summary --------

    def bar_feeds(self) -> list[tuple[str, str, str]]:
        def build() -> list[tuple[str, str, str]]:
            # FixedString(2) comes back as bytes; str(b"US") would key sessions as "b'US'".
            rows = self.query(
                "SELECT DISTINCT toString(country_code) AS country, source, resolution FROM meta.expected_series FINAL "
                "WHERE active AND contract_id IS NULL ORDER BY country, source, resolution",
                {},
            )
            return [(str(r["country"]), str(r["source"]), str(r["resolution"])) for r in rows]

        return CACHE.get("bar_feeds", build)

    def sessions(self, countries: set[str]) -> dict[str, date]:
        found = {country: expected_session(country, self.now) for country in sorted(countries)}
        return {country: day for country, day in found.items() if day is not None}

    def behind(self) -> dict[str, Any]:
        """Configured series with no bar for their country's latest completed session."""

        def build() -> dict[str, Any]:
            feeds = self.bar_feeds()
            sessions = self.sessions({feed[0] for feed in feeds})
            lagging: dict[str, set[str]] = {}
            for country, session in sessions.items():
                rows = self.query(
                    "SELECT e.listing_id AS listing_id FROM (SELECT DISTINCT listing_id, source, resolution "
                    "FROM meta.expected_series FINAL WHERE active AND contract_id IS NULL "
                    "AND country_code = {country:String}) AS e LEFT ANTI JOIN ("
                    "SELECT DISTINCT listing_id, source, resolution FROM market.bars "
                    "WHERE country_code = {country:String} AND trade_date >= {session:Date} "
                    "AND bar_time >= {since:DateTime64(3, 'UTC')}) AS b "
                    "ON b.listing_id = e.listing_id AND b.source = e.source AND b.resolution = e.resolution",
                    {
                        "country": country,
                        "session": session,
                        "since": datetime.combine(session, datetime.min.time(), UTC)
                        - timedelta(days=1),
                    },
                    max_rows=200_000,
                )
                lagging[country] = {str(row["listing_id"]) for row in rows}
            return {"sessions": sessions, "lagging": lagging}

        return CACHE.get("behind", build)

    def untagged_listings(self) -> set[str]:
        def build() -> set[str]:
            rows = self.query(
                f"SELECT listing_id FROM ({trade_sql(listing_filter='f.lid IS NOT NULL')}) "
                "WHERE untagged > 0",
                {},
                max_rows=200_000,
            )
            return {str(row["listing_id"]) for row in rows}

        return CACHE.get("untagged", build)

    # the page -----------------------------------------------------------------------------

    def page(
        self,
        *,
        search: str,
        country: str,
        exchange: str,
        scope: Scope,
        attention: Attention,
        listing_id: UUID | None,
        limit: int,
        offset: int,
        detail: bool,
    ) -> MatrixPage:
        params: dict[str, Any] = {
            "search": search,
            "country": country,
            "exchange": exchange,
            "limit": limit,
            "offset": offset,
            "held_since": self.now - HELD_LOOKBACK,
        }
        traded = (
            "listing_id IN (SELECT listing_id FROM broker.executions WHERE listing_id IS NOT NULL)"
        )
        held = (
            "listing_id IN (SELECT listing_id FROM broker.positions_snapshot WHERE listing_id IS NOT NULL "
            "AND position != 0 AND snapshot_time >= {held_since:DateTime64(3, 'UTC')})"
        )
        scopes = {
            "all": "1",
            "configured": "configured",
            "traded": traded,
            "held": held,
            "relevant": f"(configured OR {traded} OR {held})",
        }
        where = [
            "kind = 'listing'",
            scopes[scope],
            "({country:String} = '' OR country = {country:String})",
            "({exchange:String} = '' OR exchange = {exchange:String})",
            (
                "({search:String} = '' OR positionCaseInsensitiveUTF8(symbol, {search:String}) > 0 "
                "OR positionCaseInsensitiveUTF8(name, {search:String}) > 0 "
                "OR toString(id) = {search:String} "
                "OR id IN (SELECT target_id FROM ref.identifier_aliases FINAL "
                "WHERE target_kind = 'listing' "
                "AND positionCaseInsensitiveUTF8(alias_value, {search:String}) > 0))"
            ),
        ]
        if listing_id is not None:
            where.append("id = {listing:UUID}")
            params["listing"] = str(listing_id)
        if attention:
            focus = self.focus(attention, country)
            if len(focus) > MAX_FOCUS_IDS:
                raise HTTPException(
                    422, "Too many tickers need attention to list at once; filter by country first"
                )
            where.append("id IN {focus:Array(UUID)}")
            params["focus"] = sorted(focus)
        base = f"FROM ({inventory_sql()}) WHERE " + " AND ".join(where)
        total = int(self.query(f"SELECT count() AS total {base}", params)[0]["total"])
        rows = self.query(
            f"SELECT * {base} ORDER BY country, symbol, exchange, id "
            "LIMIT {limit:UInt16} OFFSET {offset:UInt32}",
            params,
        )
        instruments = [Instrument(**row) for row in rows]
        feeds = self.bar_feeds()
        columns = [
            MatrixColumn(
                key=f"bars:{c}:{s}:{r}",
                label=f"{c} · {s} · {r}",
                group="market",
                dataset="market.bars",
                level="listing",
                country=c,
                source=s,
                resolution=r,
                has_intent=True,
            )
            for c, s, r in feeds
        ] + FIXED_COLUMNS
        sessions = self.sessions({feed[0] for feed in feeds} | {i.country for i in instruments})
        cells: dict[str, dict[str, MatrixCell]] = {str(i.id): {} for i in instruments}
        unavailable: list[str] = []
        if instruments:
            groups: list[tuple[str, list[str], Callable[[], dict[str, dict[str, MatrixCell]]]]] = [
                (
                    "Bars",
                    [c.key for c in columns if c.dataset == "market.bars"],
                    lambda: self.bars(instruments, feeds, sessions),
                ),
                ("Futures bars", ["futures"], lambda: self.futures(instruments, sessions)),
                ("Fundamentals", ["fundamentals"], lambda: self.fundamentals(instruments)),
                ("Political trades", ["political"], lambda: self.political(instruments)),
                ("Holdings", ["positions"], lambda: self.positions(instruments, detail)),
                ("Trades and tags", ["trades", "tags"], lambda: self.trades(instruments, detail)),
            ]
            for label, keys, build in groups:
                try:
                    found = build()
                except Exception:  # noqa: BLE001 - one column group must not hide the others
                    unavailable.append(label)
                    found = {
                        str(i.id): {
                            key: MatrixCell(
                                state="unavailable", detail=f"{label} could not be read"
                            )
                            for key in keys
                        }
                        for i in instruments
                    }
                for listing, values in found.items():
                    cells.setdefault(listing, {}).update(values)
        return MatrixPage(
            columns=columns,
            items=[MatrixRow(instrument=i, cells=cells[str(i.id)]) for i in instruments],
            total=total,
            limit=limit,
            offset=offset,
            generated_at=self.now,
            expected_sessions=sessions,
            unavailable=unavailable,
        )

    def focus(self, attention: Attention, country: str) -> set[str]:
        if attention == "bars":
            lagging = self.behind()["lagging"]
            return set().union(*(v for k, v in lagging.items() if not country or k == country))
        return self.untagged_listings()

    # column groups ------------------------------------------------------------------------

    def bars(
        self,
        instruments: list[Instrument],
        feeds: list[tuple[str, str, str]],
        sessions: dict[str, date],
    ) -> dict[str, dict[str, MatrixCell]]:
        listing_ids = ids(i.id for i in instruments)
        intent = {
            (str(r["listing_id"]), str(r["country"]), str(r["source"]), str(r["resolution"]))
            for r in self.query(
                "SELECT DISTINCT listing_id, toString(country_code) AS country, source, resolution FROM meta.expected_series FINAL "
                "WHERE active AND contract_id IS NULL AND listing_id IN {ids:Array(UUID)}",
                {"ids": listing_ids},
            )
        }
        latest: dict[tuple[str, str, str, str], date] = {}
        for row in self.query(
            "SELECT listing_id, toString(country_code) AS country, source, resolution, max(trade_date) AS latest "
            "FROM market.bars WHERE country_code IN {countries:Array(String)} "
            "AND listing_id IN {ids:Array(UUID)} "
            "GROUP BY listing_id, country, source, resolution",
            {"ids": listing_ids, "countries": sorted({i.country for i in instruments})},
        ):
            key = (
                str(row["listing_id"]),
                str(row["country"]),
                str(row["source"]),
                str(row["resolution"]),
            )
            latest[key] = as_date(row["latest"])  # type: ignore[assignment]
        result: dict[str, dict[str, MatrixCell]] = {}
        for instrument in instruments:
            listing = str(instrument.id)
            row: dict[str, MatrixCell] = {}
            for country, source, resolution in feeds:
                key = (listing, country, source, resolution)
                configured = key in intent
                day = latest.get(key)
                state = classify(day, sessions.get(country), configured)
                detail = {
                    "ok": "Up to date",
                    "stale": f"Behind: expected {sessions.get(country)}",
                    "missing": "Configured, but no bars stored",
                    "stored": "Stored, but not configured for collection",
                    "not_configured": "Not configured",
                }.get(state, "")
                row[f"bars:{country}:{source}:{resolution}"] = MatrixCell(
                    state=state, latest=day, detail=detail
                )
            result[listing] = row
        return result

    def futures(
        self, instruments: list[Instrument], sessions: dict[str, date]
    ) -> dict[str, dict[str, MatrixCell]]:
        listing_ids = ids(i.id for i in instruments)
        intent = {
            str(r["listing_id"])
            for r in self.query(
                "SELECT DISTINCT underlying_listing_id AS listing_id FROM ref.contracts FINAL "
                "WHERE underlying_listing_id IN {ids:Array(UUID)} AND contract_id IN ("
                "SELECT contract_id FROM meta.expected_series FINAL WHERE active AND contract_id IS NOT NULL)",
                {"ids": listing_ids},
            )
        }
        stored = {
            str(r["listing_id"]): r
            for r in self.query(
                "SELECT underlying_listing_id AS listing_id, max(trade_date) AS latest, "
                "uniqExact(contract_id) AS contracts FROM market.futures_contract_bars "
                "WHERE underlying_listing_id IN {ids:Array(UUID)} GROUP BY underlying_listing_id",
                {"ids": listing_ids},
            )
        }
        result = {}
        for instrument in instruments:
            listing = str(instrument.id)
            row = stored.get(listing)
            day = as_date(row["latest"]) if row else None
            state = classify(day, sessions.get(instrument.country), listing in intent)
            result[listing] = {
                "futures": MatrixCell(
                    state=state,
                    latest=day,
                    count=int(row["contracts"]) if row else None,
                    detail=f"{int(row['contracts'])} contracts on this underlying" if row else "",
                )
            }
        return result

    def fundamentals(self, instruments: list[Instrument]) -> dict[str, dict[str, MatrixCell]]:
        stored = {
            str(r["entity_id"]): r
            for r in self.query(
                "SELECT entity_id, count() AS filings, max(filing_date) AS latest "
                "FROM fundamentals.filings FINAL WHERE entity_id IN {ids:Array(UUID)} GROUP BY entity_id",
                {"ids": ids(i.entity_id for i in instruments)},
            )
        }
        result = {}
        for instrument in instruments:
            row = stored.get(str(instrument.entity_id))
            result[str(instrument.id)] = {
                "fundamentals": MatrixCell(
                    state="ok" if row else "none",
                    latest=as_date(row["latest"]) if row else None,
                    count=int(row["filings"]) if row else 0,
                    detail="Issuer-level filings" if row else "No issuer filings stored",
                )
            }
        return result

    def political(self, instruments: list[Instrument]) -> dict[str, dict[str, MatrixCell]]:
        # Trades the political writer could not resolve to any identity still carry the
        # filing's ticker; match those on (country, ticker) and label them as such.
        unresolved = "listing_id IS NULL AND security_id IS NULL AND entity_id IS NULL"
        rows = self.query(
            "SELECT listing_id, security_id, entity_id, "
            f"if({unresolved}, ticker_raw, NULL) AS ticker, toString(country_code) AS country, "
            "count() AS trades, max(transaction_date) AS latest FROM alt.political_trades FINAL "
            "WHERE contract_id IS NULL AND (listing_id IN {listings:Array(UUID)} "
            "OR (listing_id IS NULL AND security_id IN {securities:Array(UUID)}) "
            "OR (listing_id IS NULL AND security_id IS NULL AND entity_id IN {entities:Array(UUID)}) "
            f"OR ({unresolved} AND ticker_raw IN {{symbols:Array(String)}})) "
            "GROUP BY listing_id, security_id, entity_id, ticker, country",
            {
                "listings": ids(i.id for i in instruments),
                "securities": ids(i.security_id for i in instruments),
                "entities": ids(i.entity_id for i in instruments),
                "symbols": sorted({i.symbol for i in instruments if i.symbol}),
            },
        )
        result = {}
        for instrument in instruments:
            # Most specific identity first, as in the instrument explorer (F-037).
            matches = {
                "direct": [r for r in rows if str(r["listing_id"]) == str(instrument.id)],
                "security": [
                    r
                    for r in rows
                    if r["listing_id"] is None
                    and r["security_id"] is not None
                    and str(r["security_id"]) == str(instrument.security_id)
                ],
                "issuer": [
                    r
                    for r in rows
                    if r["listing_id"] is None
                    and r["security_id"] is None
                    and r["entity_id"] is not None
                    and str(r["entity_id"]) == str(instrument.entity_id)
                ],
                "filing ticker": [
                    r
                    for r in rows
                    if r["listing_id"] is None
                    and r["security_id"] is None
                    and r["entity_id"] is None
                    and r.get("ticker") == instrument.symbol
                    and r.get("country") == instrument.country
                ],
            }
            found = [(label, r) for label, items in matches.items() for r in items]
            count = sum(int(r["trades"]) for _, r in found)
            dates = [day for _, r in found if (day := as_date(r["latest"])) is not None]
            latest = max(dates, default=None)
            labels = ", ".join(label for label, items in matches.items() if items)
            result[str(instrument.id)] = {
                "political": MatrixCell(
                    state="ok" if count else "none",
                    latest=latest,
                    count=count,
                    detail=f"Disclosed trades ({labels} match)" if count else "No disclosed trades",
                )
            }
        return result

    def positions(
        self, instruments: list[Instrument], detail: bool
    ) -> dict[str, dict[str, MatrixCell]]:
        rows = self.query(
            f"""WITH manifests AS (
                SELECT JSONExtractString(snapshot, 'broker_code') AS b,
                    JSONExtractString(snapshot, 'account_id') AS a,
                    parseDateTime64BestEffort(JSONExtractString(snapshot, 'snapshot_time'), 3, 'UTC') AS st,
                    JSONExtractString(snapshot, 'source_channel') AS sch
                FROM meta.ingestion_runs FINAL
                ARRAY JOIN JSONExtractArrayRaw(metadata_json, 'broker_snapshots') AS snapshot
                WHERE pipeline IN {SNAPSHOT_PIPELINES} AND status != 'running'
                  AND JSONExtractString(snapshot, 'dataset') = 'positions'),
            latest AS (
                SELECT b, a, argMax(sch, st) AS ch, max(st) AS t FROM manifests GROUP BY b, a
                UNION ALL
                SELECT broker_code, account_id, argMax(source_channel, snapshot_time), max(snapshot_time)
                FROM broker.positions_snapshot
                WHERE (broker_code, account_id) NOT IN (SELECT b, a FROM manifests)
                GROUP BY broker_code, account_id)
            SELECT listing_id, security_id, broker_code, account_id, account_mode, currency,
                snapshot_time, sum(position) AS quantity, sum(market_value) AS value
            FROM broker.positions_snapshot FINAL
            WHERE (broker_code, account_id, snapshot_time, source_channel) IN (SELECT b, a, t, ch FROM latest)
              AND position != 0 AND contract_id IS NULL
              AND (listing_id IN {{listings:Array(UUID)}}
                OR (listing_id IS NULL AND security_id IN {{securities:Array(UUID)}}))
            GROUP BY listing_id, security_id, broker_code, account_id, account_mode, currency, snapshot_time""",
            {
                "listings": ids(i.id for i in instruments),
                "securities": ids(i.security_id for i in instruments),
            },
        )
        result = {}
        for instrument in instruments:
            mine = [
                r
                for r in rows
                if str(r["listing_id"]) == str(instrument.id)
                or (
                    r["listing_id"] is None and str(r["security_id"]) == str(instrument.security_id)
                )
            ]
            accounts = {(r["broker_code"], r["account_id"]) for r in mine}
            stale = any(broker_snapshot_stale(r["snapshot_time"], self.now) for r in mine)
            # Per account and currency only: holdings are never summed across either.
            items = (
                [
                    {
                        "broker_code": r["broker_code"],
                        "account_id": r["account_id"],
                        "account_mode": r["account_mode"],
                        "currency": r["currency"],
                        "position": str(r["quantity"]),
                        "market_value": None if r["value"] is None else str(r["value"]),
                        "snapshot_time": r["snapshot_time"],
                    }
                    for r in mine
                ]
                if detail
                else []
            )
            result[str(instrument.id)] = {
                "positions": MatrixCell(
                    state=("stale" if stale else "ok") if mine else "none",
                    latest=max((r["snapshot_time"] for r in mine), default=None),
                    count=len(accounts),
                    detail=(
                        f"Held in {len(accounts)} account{'s' if len(accounts) != 1 else ''}"
                        + (" · snapshot missed" if stale else "")
                    )
                    if mine
                    else "Not held in the latest snapshots",
                    items=items,
                )
            }
        return result

    def trades(
        self, instruments: list[Instrument], detail: bool
    ) -> dict[str, dict[str, MatrixCell]]:
        rows = self.query(
            trade_sql(
                listing_filter="(f.lid IN {listings:Array(UUID)} OR "
                "(f.lid IS NULL AND f.sid IN {securities:Array(UUID)}))",
                by_account=True,
            ),
            {
                "listings": ids(i.id for i in instruments),
                "securities": ids(i.security_id for i in instruments),
            },
        )
        result = {}
        for instrument in instruments:
            mine = [
                r
                for r in rows
                if str(r["listing_id"]) == str(instrument.id)
                or (
                    r["listing_id"] is None and str(r["security_id"]) == str(instrument.security_id)
                )
            ]
            orders = sum(int(r["orders"]) for r in mine)
            untagged = sum(int(r["untagged"]) for r in mine)
            taggable = sum(int(r["taggable"]) for r in mine)
            latest = max((r["latest"] for r in mine), default=None)
            items = (
                [
                    {
                        k: r[k]
                        for k in (
                            "broker_code",
                            "account_id",
                            "orders",
                            "untagged",
                            "taggable",
                            "latest",
                        )
                    }
                    for r in mine
                ]
                if detail
                else []
            )
            result[str(instrument.id)] = {
                "trades": MatrixCell(
                    state="ok" if orders else "none",
                    latest=latest,
                    count=orders,
                    detail=f"{orders} orders" if orders else "No recorded trades",
                    items=items,
                ),
                "tags": MatrixCell(
                    state="none" if not taggable else "needs_tags" if untagged else "ok",
                    count=untagged if taggable else None,
                    detail=(
                        f"{untagged} of {taggable} orders need tags"
                        if untagged
                        else f"All {taggable} orders tagged"
                    )
                    if taggable
                    else "No taggable orders",
                ),
            }
        return result


def trade_sql(*, listing_filter: str, by_account: bool = False) -> str:
    """Orders per listing with tag status for the account owner's profile.

    Fills are de-duplicated by exec_id with ``argMax(..., version)``: ``FINAL`` would keep a
    corrected fill twice because ``exec_time`` is part of the sort key.
    """
    account = ", f.broker_code AS broker_code, f.account_id AS account_id" if by_account else ""
    account_group = ", f.broker_code, f.account_id" if by_account else ""
    return f"""SELECT f.lid AS listing_id, f.sid AS security_id{account},
        uniqExact(tuple(f.broker_code, f.account_id, f.order_key)) AS orders,
        uniqExactIf(tuple(f.broker_code, f.account_id, f.order_key), f.perm > 0 AND o.owned = 1) AS taggable,
        uniqExactIf(tuple(f.broker_code, f.account_id, f.order_key),
            f.perm > 0 AND o.owned = 1 AND t.tagged = 0) AS untagged,
        max(f.exec_at) AS latest
    FROM (
        SELECT broker_code, account_id, exec_id,
            argMax(perm_id, version) AS perm, argMax(listing_id, version) AS lid,
            argMax(security_id, version) AS sid, argMax(contract_id, version) AS contract,
            argMax(exec_time, version) AS exec_at,
            if(perm > 0, toString(perm), concat('fill:', exec_id)) AS order_key
        FROM broker.executions GROUP BY broker_code, account_id, exec_id
    ) AS f
    LEFT JOIN (
        SELECT broker_code, account_id, 1 AS owned FROM ref.broker_account_owners FINAL
    ) AS o ON o.broker_code = f.broker_code AND o.account_id = f.account_id
    LEFT JOIN (
        SELECT a.broker_code AS broker_code, a.account_id AS account_id, a.perm_id AS perm_id,
            argMax(isNotNull(a.theme_id), tuple(a.changed_at, a.event_id)) AS tagged
        FROM book.order_assignment_events AS a
        INNER JOIN ref.broker_account_owners AS w FINAL
            ON w.broker_code = a.broker_code AND w.account_id = a.account_id AND w.user_id = a.user_id
        GROUP BY a.broker_code, a.account_id, a.perm_id
    ) AS t ON t.broker_code = f.broker_code AND t.account_id = f.account_id AND t.perm_id = f.perm
    WHERE f.contract IS NULL AND {listing_filter}
    GROUP BY f.lid, f.sid{account_group}"""


def repository() -> Any:
    db = ClickHouse.from_environment()
    try:
        yield MatrixRepository(db.client)
    finally:
        db.close()


Repo = Annotated[MatrixRepository, Depends(repository)]
router = APIRouter()


@router.get("/matrix", response_model=MatrixPage)
def matrix(
    repo: Repo,
    search: Annotated[str, Query(max_length=200)] = "",
    country: Annotated[str, Query(max_length=2)] = "",
    exchange: Annotated[str, Query(max_length=50)] = "",
    scope: Scope = "relevant",
    attention: Attention = "",
    listing_id: UUID | None = None,
    detail: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> MatrixPage:
    try:
        return repo.page(
            search=search.strip(),
            country=country,
            exchange=exchange,
            scope=scope,
            attention=attention,
            listing_id=listing_id,
            limit=limit,
            offset=offset,
            detail=detail,
        )
    except CatalogError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def ticker_lag(client: Any, now: datetime) -> dict[str, Any]:
    """For the monitoring summary: configured tickers behind their latest session, per country."""
    return MatrixRepository(client, now).behind()


__all__ = ["CACHE", "MatrixPage", "MatrixRepository", "expected_session", "router", "ticker_lag"]
