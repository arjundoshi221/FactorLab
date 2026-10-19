"""Operator commands for profile registration and synthetic broker ingestion."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from factorlab.clickhouse import ClickHouse
from factorlab.ingest.profiles import ProfileRegistry
from factorlab.ingest.provider import run_provider
from factorlab.sources.ibkr.provider import DryRunBrokerStorage
from factorlab.sources.ibkr.simulation import STEPS, simulated_provider
from factorlab.storage.profiles import ProfileStorage
from factorlab.storage.v2_broker import V2BrokerStorage


def load_registry(path: Path) -> ProfileRegistry:
    return ProfileRegistry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def profiles_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factorlab-ingest-broker profiles")
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    registry = load_registry(args.config)
    db = ClickHouse.from_environment()
    try:
        result = ProfileStorage(db.client).sync(registry, dry_run=args.dry_run)
        print(json.dumps({"dry_run": args.dry_run, **result}))
    finally:
        db.close()
    return 0


def simulate_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factorlab-ingest-broker simulate")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario", choices=["basic"], default="basic")
    parser.add_argument("--step", choices=["all", *STEPS], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    registry = load_registry(args.config)
    accounts = [account for account in registry.accounts if account.simulated and account.active]
    if not accounts:
        parser.error("configuration has no active simulated accounts")
    if any(
        account.base_currency != "USD" or account.booking_country != "US" for account in accounts
    ):
        parser.error("basic scenario requires US-booked USD accounts")
    db = None if args.dry_run else V2BrokerStorage.from_environment()
    storage = db if db is not None else DryRunBrokerStorage()
    try:
        if db is not None:
            ProfileStorage(db.client).sync(registry)
        steps = STEPS if args.step == "all" else (args.step,)
        start = datetime.now(UTC) - timedelta(seconds=len(steps))
        failed = False
        for index, step in enumerate(steps):
            for account in accounts:
                at = start + timedelta(seconds=index)
                provider = simulated_provider(
                    storage,
                    account,
                    step,
                    at=at,
                    quantity=10 * (1 + sum(account.account_id.encode()) % 5),
                )
                summary = run_provider(
                    provider,
                    storage,
                    universe="simulated_accounts",
                    requested_series=4,
                    metadata={
                        "simulated": True,
                        "account_id": account.account_id,
                        "user_id": str(account.user_id),
                        "scenario": "basic",
                        "step": step,
                    },
                )
                print(
                    json.dumps(
                        {
                            "account_id": account.account_id,
                            "step": step,
                            "status": summary.status,
                            "rows": summary.rows_written,
                            "dry_run": args.dry_run,
                        }
                    )
                )
                failed = failed or summary.status != "success"
        return 1 if failed else 0
    finally:
        if db is not None:
            db.close()
