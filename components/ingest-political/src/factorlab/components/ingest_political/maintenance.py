"""One-off identity repairs, dry-run unless ``--apply`` (docs/operations/identity-repair.md).

``dedupe-political-trades`` deletes trade rows superseded by a newer version under
another identity. ``retire-duplicate-listings`` retires provable duplicate listing
identities into the listing that carries the canonical provider alias.
``reresolve-political-trades`` then re-links trades that were unresolved or pointed at a
retired duplicate. ``purge-retired-listings`` finally deletes the retired tombstones.
Each prints a JSON report; run the dry run first and review it.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from factorlab.core.logging import configure_logging


def _print(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def retire_main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-political", service="identity-repair")
    parser = argparse.ArgumentParser(description="Retire duplicate listing identities")
    parser.add_argument("--country", default="US")
    parser.add_argument("--canonical-alias", default="schwab_symbol")
    parser.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = parser.parse_args(argv)
    from factorlab.storage.identity_repair import retire_duplicate_listings
    from factorlab.storage.sinks import ClickHouseSinks

    sink = ClickHouseSinks.from_environment("USA")
    try:
        report = retire_duplicate_listings(
            sink.client,
            country=args.country,
            canonical_alias_kind=args.canonical_alias,
            apply=args.apply,
        )
    finally:
        sink.close()
    _print(report.as_dict())
    return 0


def purge_main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-political", service="identity-repair")
    parser = argparse.ArgumentParser(description="Purge retired duplicate listing identities")
    parser.add_argument("--country", default="US")
    parser.add_argument("--apply", action="store_true", help="delete; default is a dry run")
    args = parser.parse_args(argv)
    from factorlab.storage.identity_repair import purge_retired_listings
    from factorlab.storage.sinks import ClickHouseSinks

    sink = ClickHouseSinks.from_environment("USA")
    try:
        report = purge_retired_listings(sink.client, country=args.country, apply=args.apply)
    finally:
        sink.close()
    _print(report.as_dict())
    return 0


def dedupe_main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-political", service="identity-repair")
    parser = argparse.ArgumentParser(description="Delete superseded political trade rows")
    parser.add_argument("--apply", action="store_true", help="delete; default is a dry run")
    args = parser.parse_args(argv)
    from factorlab.storage.sinks import ClickHouseSinks

    sink = ClickHouseSinks.from_environment("USA")
    try:
        report = sink.dedupe_political_trades(apply=args.apply)
    finally:
        sink.close()
    _print(report)
    return 0


def reresolve_main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-political", service="identity-repair")
    parser = argparse.ArgumentParser(description="Re-resolve political trades to listings")
    parser.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = parser.parse_args(argv)
    from factorlab.storage.sinks import ClickHouseSinks

    sink = ClickHouseSinks.from_environment("USA")
    try:
        report = sink.reresolve_political_trades(apply=args.apply)
    finally:
        sink.close()
    _print(report)
    return 0
