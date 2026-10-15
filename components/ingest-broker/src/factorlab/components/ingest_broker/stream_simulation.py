"""Timed synthetic trade replay, with resumable account-scoped checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factorlab.components.ingest_broker.profiles import load_registry
from factorlab.core import paths
from factorlab.ingest.profiles import ProfileRegistry
from factorlab.ingest.provider import run_provider
from factorlab.runtime import acquire_lock
from factorlab.sources.ibkr.provider import DryRunBrokerStorage
from factorlab.sources.ibkr.stream_simulation import StreamGateway, StreamScenario, stream_provider
from factorlab.storage.profiles import ProfileStorage
from factorlab.storage.v2_broker import V2BrokerStorage


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    fingerprint: str
    session: str
    started_at: datetime
    next_event: int = Field(default=0, ge=0)
    event_times: list[datetime] = Field(default_factory=list)


def save_checkpoint(path: Path | None, checkpoint: Checkpoint) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def run_stream(
    registry: ProfileRegistry,
    scenario: StreamScenario,
    storage: Any,
    *,
    session: str,
    interval: float,
    state_path: Path | None = None,
    max_events: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if len(registry.accounts) != 1 or len(registry.users) != 1:
        raise ValueError("stream config must contain exactly one test user and one account")
    account = registry.accounts[0]
    if not registry.users[0].active:
        raise ValueError("the test profile must be active")
    # Validate the whole scenario before emitting any captures or changing checkpoints.
    validation = StreamGateway(account, scenario, session)
    now = datetime.now(UTC)
    for index, event in enumerate(scenario.events):
        validation.apply(event, index, now)
    fingerprint = hashlib.sha256(
        (scenario.model_dump_json() + account.model_dump_json()).encode()
    ).hexdigest()
    checkpoint = Checkpoint(fingerprint=fingerprint, session=session, started_at=now)
    if state_path is not None and state_path.exists():
        checkpoint = Checkpoint.model_validate_json(state_path.read_text(encoding="utf-8"))
        if checkpoint.fingerprint != fingerprint or checkpoint.session != session:
            raise ValueError("scenario/account changed for this session; choose a new session ID")
        if (
            checkpoint.next_event > len(scenario.events)
            or len(checkpoint.event_times)
            not in (
                checkpoint.next_event,
                checkpoint.next_event + 1,
            )
            or any(
                stamp.tzinfo is None for stamp in [checkpoint.started_at, *checkpoint.event_times]
            )
        ):
            raise ValueError("invalid simulation checkpoint")
    gateway = StreamGateway(account, scenario, session)
    for index in range(checkpoint.next_event):
        gateway.apply(scenario.events[index], index, checkpoint.event_times[index])
    save_checkpoint(state_path, checkpoint)
    stop = len(scenario.events)
    if max_events is not None:
        stop = min(stop, checkpoint.next_event + max_events)
    initial_index = checkpoint.next_event
    for index in range(initial_index, stop):
        if index > initial_index and interval:
            sleep(interval)
        if len(checkpoint.event_times) == index:
            checkpoint.event_times.append(datetime.now(UTC))
            # A retry must reuse the same fill timestamp, even after a partial DB write.
            save_checkpoint(state_path, checkpoint)
        event = scenario.events[index]
        gateway.apply(event, index, checkpoint.event_times[index])
        summary = run_provider(
            stream_provider(storage, gateway, datetime.now(UTC), checkpoint.started_at),
            storage,
            universe="simulated_accounts",
            requested_series=4,
            metadata={
                "simulated": True,
                "account_id": account.account_id,
                "user_id": str(account.user_id),
                "scenario": "trade_stream",
                "session": session,
                "event_index": index,
                "event_kind": event.kind,
            },
        )
        print(
            json.dumps(
                {
                    "account_id": account.account_id,
                    "session": session,
                    "event": index + 1,
                    "of": len(scenario.events),
                    "action": event.model_dump(mode="json"),
                    "status": summary.status,
                    "cash": str(gateway.cash),
                    "positions": {
                        symbol: str(item.quantity)
                        for symbol, item in gateway.holdings.items()
                        if item.quantity
                    },
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if summary.status != "success":
            print(
                "Snapshot failed; progress was not advanced. Run the same session to retry.",
                flush=True,
            )
            return 1
        checkpoint.next_event = index + 1
        save_checkpoint(state_path, checkpoint)
    print(
        f"Session {session}: {checkpoint.next_event}/{len(scenario.events)} events complete.",
        flush=True,
    )
    return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--session", default="demo")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--state-dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="register the test account and write snapshots to configured ClickHouse",
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="no DB connection or checkpoint writes (default)"
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.session):
        parser.error("session must contain 1-48 letters, digits, underscores or hyphens")
    if not math.isfinite(args.interval) or not 0 <= args.interval <= 3600:
        parser.error("interval must be between 0 and 3600 seconds")
    if args.max_events is not None and args.max_events < 1:
        parser.error("max-events must be positive")
    registry = load_registry(args.config)
    scenario = StreamScenario.model_validate(
        yaml.safe_load(args.scenario.read_text(encoding="utf-8"))
    )
    try:
        if not args.write:
            return run_stream(
                registry,
                scenario,
                DryRunBrokerStorage(),
                session=args.session,
                interval=args.interval,
                max_events=args.max_events,
            )
        if len(registry.accounts) != 1 or len(registry.users) != 1:
            parser.error("stream config must contain exactly one test user and one account")
        if not registry.users[0].active:
            parser.error("the test profile must be active")
        account = registry.accounts[0]
        # Refuse real-account registration before opening a DB connection.
        validation = StreamGateway(account, scenario, args.session)
        for index, event in enumerate(scenario.events):
            validation.apply(event, index, datetime.now(UTC))
        account_key = hashlib.sha256(
            f"{account.broker_code}:{account.account_id}".encode()
        ).hexdigest()[:24]
        root = (args.state_dir or paths.state_dir("ibkr-simulation")) / account_key
        with acquire_lock(root / "stream.lock"):
            db = V2BrokerStorage.from_environment()
            try:
                ProfileStorage(db.client).sync(registry)
                print(
                    f"Writing synthetic account {account.account_id}; session state: {root / (args.session + '.json')}",
                    flush=True,
                )
                return run_stream(
                    registry,
                    scenario,
                    db,
                    session=args.session,
                    interval=args.interval,
                    state_path=root / f"{args.session}.json",
                    max_events=args.max_events,
                )
            finally:
                db.close()
    except KeyboardInterrupt:
        print("Stopped. Run the same write session to resume.", flush=True)
        return 130
