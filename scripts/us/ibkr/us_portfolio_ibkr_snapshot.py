"""Snapshot IBKR portfolio state to local Parquet.

For each mode in {paper, live}: connect to the appropriate Gateway,
capture positions + account state + today's executions + open orders,
and write Parquet files under ``data/ibkr/{account_mode}/{yyyy-mm-dd}/``.
A manifest JSON records counts, timings, and IBKR server versions.

Intended cadence (user schedules externally — see
``memory/feedback_no_task_scheduler.md``): pre-open 06:00 ET and EOD
16:30 ET. This pass writes files only; ClickHouse ingest lands in Wave 7.

Usage:
    "C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/ibkr/us_portfolio_ibkr_snapshot.py
    "C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/ibkr/us_portfolio_ibkr_snapshot.py --modes paper
    "C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/ibkr/us_portfolio_ibkr_snapshot.py --dry-run
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd

from factorlab.sources.ibkr import (
    Mode,
    connect,
    disconnect,
    pull_executions,
    snapshot_account_state,
    snapshot_open_orders,
    snapshot_positions,
)

log = logging.getLogger("us_portfolio_ibkr_snapshot")

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data" / "ibkr"

FILES = {
    "positions": "positions.parquet",
    "account_state": "account_state.parquet",
    "executions": "executions.parquet",
    "open_orders": "open_orders.parquet",
}


def _row_to_dict(row: Any) -> dict[str, Any]:
    if not is_dataclass(row):
        raise TypeError(f"Expected dataclass row, got {type(row)!r}")
    out: dict[str, Any] = {}
    for f in dataclasses.fields(row):
        v = getattr(row, f.name)
        if isinstance(v, Decimal):
            out[f.name] = float(v)  # parquet-friendly; canonical value stays Decimal in-memory
        elif isinstance(v, UUID):
            out[f.name] = str(v)
        else:
            out[f.name] = v
    return out


def _to_frame(rows: list[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([_row_to_dict(r) for r in rows])


def snapshot_one_mode(mode: Mode, run_stamp: datetime, *, dry_run: bool) -> dict[str, Any]:
    """Connect + snapshot one Gateway. Returns a manifest fragment."""
    day_dir = DATA_ROOT / mode / run_stamp.strftime("%Y-%m-%d")
    result: dict[str, Any] = {
        "mode": mode,
        "day_dir": str(day_dir),
        "started_at": datetime.now(UTC).isoformat(),
        "counts": {},
        "timings_sec": {},
        "server_version": None,
        "status": "pending",
        "error": None,
    }
    try:
        t_connect = time.time()
        ib = connect(mode=mode, readonly=True)
        result["timings_sec"]["connect"] = round(time.time() - t_connect, 3)
        result["server_version"] = ib.client.serverVersion()
    except Exception as exc:  # noqa: BLE001 — top-level orchestration
        log.warning("[%s] Gateway unreachable, skipping: %s", mode, exc)
        result["status"] = "skipped"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    try:
        t0 = time.time()
        positions = snapshot_positions(ib, snapshot_time=run_stamp)
        result["timings_sec"]["positions"] = round(time.time() - t0, 3)

        t0 = time.time()
        account_state = snapshot_account_state(ib, snapshot_time=run_stamp)
        result["timings_sec"]["account_state"] = round(time.time() - t0, 3)

        t0 = time.time()
        execs = pull_executions(ib, since=run_stamp - timedelta(days=1), now=run_stamp)
        result["timings_sec"]["executions"] = round(time.time() - t0, 3)

        t0 = time.time()
        open_orders = snapshot_open_orders(ib, snapshot_time=run_stamp)
        result["timings_sec"]["open_orders"] = round(time.time() - t0, 3)

        result["counts"] = {
            "positions": len(positions),
            "account_state": len(account_state),
            "executions": len(execs),
            "open_orders": len(open_orders),
        }

        if dry_run:
            log.info("[%s] DRY-RUN — would write to %s: %s", mode, day_dir, result["counts"])
        else:
            day_dir.mkdir(parents=True, exist_ok=True)
            _to_frame(positions).to_parquet(day_dir / FILES["positions"], index=False)
            _to_frame(account_state).to_parquet(day_dir / FILES["account_state"], index=False)
            _to_frame(execs).to_parquet(day_dir / FILES["executions"], index=False)
            _to_frame(open_orders).to_parquet(day_dir / FILES["open_orders"], index=False)
            log.info("[%s] wrote %s (%s)", mode, day_dir, result["counts"])

        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        log.exception("[%s] snapshot failed", mode)
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        disconnect(ib)
        result["finished_at"] = datetime.now(UTC).isoformat()

    return result


def _parse_modes(raw: str) -> list[Mode]:
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    for t in tokens:
        if t not in ("paper", "live"):
            raise argparse.ArgumentTypeError(f"invalid mode {t!r} (want 'paper' or 'live')")
    return tokens  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modes", default="paper,live", type=_parse_modes,
        help="Comma-separated modes to snapshot (default: paper,live)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log what would be written; don't write")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    run_stamp = datetime.now(UTC)
    manifest: dict[str, Any] = {
        "run_stamp": run_stamp.isoformat(),
        "modes_requested": list(args.modes),
        "dry_run": bool(args.dry_run),
        "results": {},
    }

    any_failed = False
    for mode in args.modes:
        res = snapshot_one_mode(mode, run_stamp, dry_run=args.dry_run)
        manifest["results"][mode] = res
        if res["status"] == "failed":
            any_failed = True

    if not args.dry_run:
        for mode, res in manifest["results"].items():
            if res["status"] == "skipped":
                continue
            day_dir = Path(res["day_dir"])
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print(json.dumps(manifest, indent=2, default=str))
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
