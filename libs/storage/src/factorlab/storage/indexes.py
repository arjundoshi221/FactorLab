"""ClickHouse reads and writes for constructed indexes (ADR 0017).

Reads the inputs ``factorlab.indexes`` needs (membership and sector spans, shares
observations, daily closes year by year) and writes ``derived.index_levels``,
``derived.index_weights`` and a ``derived.computations`` row per run. Closes come from
``market.bars`` for the caller's daily source: ``market.bars_best`` aggregates on read
and needs ``ref.source_priorities``, which a full-universe scan should not depend on.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from factorlab.storage.clickhouse import _decoded_text, _version

INSERT_BATCH = 100_000


class IndexStorage:
    def __init__(self, client: Any) -> None:
        self.client = client

    # -- inputs -----------------------------------------------------------------
    def universe_spans(self, universe: str) -> list[tuple[UUID, date, date | None]]:
        return [
            (row[0], row[1], row[2])
            for row in self.client.query(
                "SELECT listing_id, effective_from, effective_to "
                "FROM ref.universe_membership FINAL WHERE universe_id = {universe:String}",
                parameters={"universe": universe},
            ).result_rows
        ]

    def sector_spans(self, classification: str) -> list[tuple[UUID, str, date, date | None]]:
        return [
            (row[0], _decoded_text(row[1]), row[2], row[3])
            for row in self.client.query(
                "SELECT listing_id, sector_id, effective_from, effective_to "
                "FROM ref.security_sector_history FINAL "
                "WHERE classification = {classification:String}",
                parameters={"classification": classification},
            ).result_rows
        ]

    def shares(self, listings: Sequence[UUID]) -> dict[UUID, list[tuple[date, float]]]:
        """Every observation per listing; several sources on one day keep the largest."""
        out: dict[UUID, list[tuple[date, float]]] = {}
        if not listings:
            return out
        for listing, day, count in self.client.query(
            "SELECT listing_id, as_of_date, max(shares_outstanding) "
            "FROM fundamentals.shares_outstanding FINAL "
            "WHERE listing_id IN {ids:Array(UUID)} GROUP BY listing_id, as_of_date",
            parameters={"ids": list(listings)},
        ).result_rows:
            out.setdefault(listing, []).append((day, float(count)))
        return out

    def closes(
        self, listings: Sequence[UUID], start: date, end: date, *, source: str
    ) -> Iterator[tuple[date, dict[UUID, float]]]:
        """Daily closes grouped by trade date, ascending, read one calendar year at a time."""
        if not listings:
            return
        for year in range(start.year, end.year + 1):
            lo, hi = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
            rows = self.client.query(
                "SELECT trade_date, listing_id, toFloat64(close) FROM market.bars FINAL "
                "WHERE country_code = 'US' AND resolution = 'daily' "
                "AND source = {source:String} AND listing_id IN {ids:Array(UUID)} "
                "AND trade_date BETWEEN {lo:Date} AND {hi:Date} AND close > 0 "
                "ORDER BY trade_date",
                parameters={"source": source, "ids": list(listings), "lo": lo, "hi": hi},
            ).result_rows
            day: date | None = None
            batch: dict[UUID, float] = {}
            for trade_date, listing, close in rows:
                if trade_date != day:
                    if day is not None:
                        yield day, batch
                    day, batch = trade_date, {}
                batch[listing] = close
            if day is not None:
                yield day, batch

    # -- outputs ----------------------------------------------------------------
    def start_computation(
        self, *, dataset: str, code_version: str, parameters: dict[str, Any]
    ) -> UUID:
        computation_id = uuid4()
        now = datetime.now(UTC)
        text = json.dumps(parameters, sort_keys=True, default=str)
        self._insert(
            "derived.computations",
            [
                {
                    "computation_id": computation_id,
                    "dataset": dataset,
                    "code_version": code_version,
                    "input_query": "market.bars daily closes, ref.universe_membership, "
                    "ref.security_sector_history, fundamentals.shares_outstanding",
                    "input_hash": hashlib.sha256(text.encode()).hexdigest(),
                    "input_row_count": 0,
                    "parameters": text,
                    "row_count": 0,
                    "started_at": now,
                    "completed_at": None,
                    "status": "running",
                    "error": None,
                    "version": _version(now),
                    "ingested_at": now,
                }
            ],
        )
        self._started = (computation_id, dataset, code_version, text, now)
        return computation_id

    def finish_computation(
        self, *, input_rows: int, rows: int, status: str, error: str | None = None
    ) -> None:
        computation_id, dataset, code_version, text, started = self._started
        now = datetime.now(UTC)
        self._insert(
            "derived.computations",
            [
                {
                    "computation_id": computation_id,
                    "dataset": dataset,
                    "code_version": code_version,
                    "input_query": "market.bars daily closes, ref.universe_membership, "
                    "ref.security_sector_history, fundamentals.shares_outstanding",
                    "input_hash": hashlib.sha256(text.encode()).hexdigest(),
                    "input_row_count": input_rows,
                    "parameters": text,
                    "row_count": rows,
                    "started_at": started,
                    "completed_at": now,
                    "status": status,
                    "error": error,
                    "version": _version(now),
                    "ingested_at": now,
                }
            ],
        )

    def write_levels(self, rows: Sequence[dict[str, Any]]) -> None:
        self._insert("derived.index_levels", list(rows))

    def write_weights(self, rows: Sequence[dict[str, Any]]) -> None:
        for i in range(0, len(rows), INSERT_BATCH):
            self._insert("derived.index_weights", list(rows[i : i + INSERT_BATCH]))

    def _insert(self, table: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        columns = list(records[0])
        self.client.insert(
            table, [[row[c] for c in columns] for row in records], column_names=columns
        )


__all__ = ["IndexStorage"]
