"""Run from the repository root: python simulations/ibkr/run.py --help."""

import sys
from pathlib import Path

from factorlab.components.ingest_broker.stream_simulation import cli

if __name__ == "__main__":
    folder = Path(__file__).resolve().parent
    raise SystemExit(
        cli(
            [
                "--config",
                str(folder / "account.yaml"),
                "--scenario",
                str(folder / "trades.yaml"),
                *sys.argv[1:],
            ]
        )
    )
