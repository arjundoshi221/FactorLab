"""Move live tables onto the wave 16 yearly partitions and swap their names.

    factorlab-db repartition plan            show each table's layout and copy state
    factorlab-db repartition apply --yes     copy, verify and EXCHANGE (writers stopped)

Monthly partitions for daily history made every refresh create ~680 parts per table
and OOM-killed ClickHouse (2026-10). Wave 16 creates ``*_v16`` tables that differ only
in their partition key. ``apply`` refuses while anything still writes to a source
table, copies it one source partition at a time (bounded memory), compares the
deduplicated row count and a key-and-version hash, then runs ``EXCHANGE TABLES``.
Afterwards the ``*_v16`` names hold the pre-swap data as the rollback; dropping them
is a separate operator decision. A run interrupted before the swap starts over: the
target is never live until the exchange, so it is emptied first.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from factorlab.schema.migrate import MigrationError, create_client, migration_lock


@dataclass(frozen=True)
class Swap:
    live: str
    target: str
    # The sort key plus version: rows a ReplacingMergeTree keeps are identical on these,
    # whichever duplicate a merge retained.
    identity: str
    yearly_marker: str  # text only the wave 16 partition key contains


SWAPS = (
    Swap(
        "market.bars",
        "market.bars_v16",
        "country_code, product_type, listing_id, resolution, source, bar_time, version",
        "toYear(bar_time)",
    ),
    Swap(
        "meta.session_coverage",
        "meta.session_coverage_v16",
        "country_code, listing_id, source, resolution, trade_date, version",
        "toYear(trade_date)",
    ),
)
# A write within this window means a collector is still running against the table.
QUIET_SECONDS = 120
COPY_SETTINGS = {"max_insert_threads": 1, "max_threads": 2}
VERIFY_SETTINGS = {"do_not_merge_across_partitions_select_final": 1, "max_threads": 2}


def _split(table: str) -> tuple[str, str]:
    database, name = table.split(".", 1)
    return database, name


def _value(client: Any, sql: str, parameters: dict[str, Any] | None = None) -> Any:
    rows = client.query(sql, parameters=parameters or {}).result_rows
    return rows[0][0] if rows else None


def partition_key(client: Any, table: str) -> str | None:
    database, name = _split(table)
    return _value(
        client,
        "SELECT partition_key FROM system.tables WHERE database = {db:String} AND name = {t:String}",
        {"db": database, "t": name},
    )


def columns(client: Any, table: str) -> list[tuple[str, str]]:
    database, name = _split(table)
    result = client.query(
        "SELECT name, type FROM system.columns WHERE database = {db:String} "
        "AND table = {t:String} ORDER BY position",
        parameters={"db": database, "t": name},
    )
    return [(row[0], row[1]) for row in result.result_rows]


def source_partitions(client: Any, table: str) -> list[tuple[str, int]]:
    database, name = _split(table)
    result = client.query(
        "SELECT partition_id, sum(rows) FROM system.parts WHERE active "
        "AND database = {db:String} AND table = {t:String} "
        "GROUP BY partition_id ORDER BY partition_id",
        parameters={"db": database, "t": name},
    )
    return [(row[0], int(row[1])) for row in result.result_rows]


def recent_writes(client: Any, table: str) -> int:
    database, name = _split(table)
    parts = _value(
        client,
        "SELECT count() FROM system.part_log WHERE event_type = 'NewPart' "
        "AND database = {db:String} AND table = {t:String} "
        "AND event_time > now() - toIntervalSecond({quiet:UInt32})",
        {"db": database, "t": name, "quiet": QUIET_SECONDS},
    )
    running = _value(
        client,
        "SELECT count() FROM system.processes WHERE query_kind = 'Insert' "
        "AND positionCaseInsensitive(query, {needle:String}) > 0",
        {"needle": f"INSERT INTO {table} "},
    )
    return int(parts or 0) + int(running or 0)


def fingerprint(client: Any, table: str, identity: str) -> tuple[int, int]:
    rows = client.query(
        f"SELECT count(), sum(cityHash64({identity})) FROM {table} FINAL",
        settings=VERIFY_SETTINGS,
    ).result_rows
    return int(rows[0][0]), int(rows[0][1] or 0)


def state(client: Any, swap: Swap) -> str:
    live = partition_key(client, swap.live)
    target = partition_key(client, swap.target)
    if live is None:
        raise MigrationError(f"{swap.live} does not exist")
    if swap.yearly_marker in live:
        return "swapped"
    if target is None:
        raise MigrationError(f"{swap.target} is missing; apply wave 16 schema first")
    if swap.yearly_marker not in target:
        raise MigrationError(f"{swap.target} does not carry the wave 16 partition key")
    return "pending"


def apply_swap(client: Any, swap: Swap, *, out: Callable[[str], None] = print) -> None:
    if state(client, swap) == "swapped":
        out(f"{swap.live}: already on yearly partitions")
        return
    if columns(client, swap.live) != columns(client, swap.target):
        raise MigrationError(f"{swap.live} and {swap.target} have different columns")
    if writes := recent_writes(client, swap.live):
        raise MigrationError(
            f"{swap.live} had {writes} writes in the last {QUIET_SECONDS}s; stop its writers"
        )
    client.command(f"TRUNCATE TABLE {swap.target}")
    partitions = source_partitions(client, swap.live)
    for number, (partition_id, rows) in enumerate(partitions, 1):
        client.command(
            f"INSERT INTO {swap.target} SELECT * FROM {swap.live} "
            "WHERE _partition_id = {partition:String}",
            parameters={"partition": partition_id},
            settings=COPY_SETTINGS,
        )
        if number % 50 == 0 or number == len(partitions):
            out(f"{swap.live}: copied {number}/{len(partitions)} partitions ({rows} rows last)")
    if writes := recent_writes(client, swap.live):
        raise MigrationError(f"{swap.live} was written during the copy; nothing was swapped")
    expected = fingerprint(client, swap.live, swap.identity)
    copied = fingerprint(client, swap.target, swap.identity)
    if expected != copied:
        raise MigrationError(
            f"{swap.target} does not match {swap.live} (rows/hash {copied} != {expected}); "
            "nothing was swapped"
        )
    client.command(f"EXCHANGE TABLES {swap.live} AND {swap.target}")
    out(
        f"{swap.live}: swapped onto yearly partitions ({expected[0]} rows); "
        f"pre-swap data kept in {swap.target}"
    )


def command_plan(client: Any, *, out: Callable[[str], None] = print) -> None:
    for swap in SWAPS:
        current = state(client, swap)
        partitions = source_partitions(client, swap.live)
        out(f"{swap.live}: {current}, {len(partitions)} partitions -> {swap.target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factorlab-db repartition", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show each table's layout and copy state")
    apply = commands.add_parser("apply", help="copy, verify and swap; writers must be stopped")
    apply.add_argument("--yes", action="store_true", help="confirm ClickHouse mutations")
    apply.add_argument("--lock-timeout", type=int, default=3600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "apply" and not args.yes:
            raise MigrationError("repartition apply changes production tables; pass --yes")
        client = create_client()
        if args.command == "plan":
            command_plan(client)
            return 0
        with migration_lock(client, args.lock_timeout):
            for swap in SWAPS:
                apply_swap(client, swap)
    except MigrationError as exc:
        print(f"repartition refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
