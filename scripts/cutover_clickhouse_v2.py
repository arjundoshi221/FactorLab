"""Validate and exchange the three canonical-keyed v2 operational tables.

Run after the final Wave 9 backfill with every legacy writer stopped. Schema
migration is a separate step. A second invocation is safe: it will not exchange
the names back to their legacy layout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.clickhouse import ClickHouseStorage

TABLES = ("expected_series", "session_coverage", "recovery_state")


def _sort_key(client, table: str) -> str:
    rows = client.query(
        "SELECT sorting_key FROM system.tables "
        "WHERE database = 'meta' AND name = {table:String}",
        parameters={"table": table},
    ).result_rows
    if len(rows) != 1:
        raise RuntimeError(f"missing meta.{table}")
    return str(rows[0][0])


def _canonical(client, table: str) -> bool:
    key = _sort_key(client, table)
    return "listing_id" in key and "legacy_instrument_id" not in key


def _count(client, table: str) -> int:
    return int(client.query(f"SELECT count() FROM meta.{table} FINAL").result_rows[0][0])


def _difference(client, source: str, destination: str) -> int:
    return int(client.query(
        f"SELECT count() FROM (SELECT * FROM meta.{source} FINAL "
        f"EXCEPT DISTINCT SELECT * FROM meta.{destination} FINAL)"
    ).result_rows[0][0])


def validate(client) -> bool:
    """Raise on a mismatch; return True when all public names are canonical."""
    states = []
    for name in TABLES:
        canonical = _canonical(client, name)
        replacement = _canonical(client, f"{name}_canonical")
        if canonical == replacement:
            raise RuntimeError(f"meta.{name} and its replacement have the same key layout")
        states.append(canonical)
    if any(states) and not all(states):
        raise RuntimeError("partial operational table exchange; inspect before continuing")
    if all(states):
        print("Canonical operational table names are already active.")
        return True
    for name in TABLES:
        replacement = f"{name}_canonical"
        left, right = _count(client, name), _count(client, replacement)
        missing = _difference(client, name, replacement)
        extra = _difference(client, replacement, name)
        print(f"meta.{name}: source={left}, replacement={right}, missing={missing}, extra={extra}")
        if left != right or missing or extra:
            raise RuntimeError(f"meta.{name} is not an exact logical copy")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "exchange"))
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.action == "exchange" and not args.yes:
        parser.error("exchange requires --yes")
    storage = ClickHouseStorage.from_environment()
    try:
        active = validate(storage.client)
        if args.action == "exchange" and not active:
            for name in TABLES:
                storage.client.command(
                    f"EXCHANGE TABLES meta.{name} AND meta.{name}_canonical"
                )
                print(f"Exchanged meta.{name}")
            if not validate(storage.client):
                raise RuntimeError("canonical table names are not active after exchange")
    finally:
        storage.close()


if __name__ == "__main__":
    main()
