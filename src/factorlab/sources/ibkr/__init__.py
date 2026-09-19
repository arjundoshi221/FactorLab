"""Interactive Brokers read-only adapter.

Mirrors broker-side truth (positions, account state, executions, open
orders) across paper and live IBKR accounts into the ``broker.*`` mirror
tables. Order placement is out of scope — see the module docstring and
``docs/data-sources/06-ibkr.md`` §4 for the three-layer read-only guard.
"""

from __future__ import annotations

from factorlab.sources.ibkr.client import (
    Mode,
    connect,
    connected,
    disconnect,
    mode_of,
    source_channel_of,
)
from factorlab.sources.ibkr.contracts import ContractCache, ContractKey, qualify_contracts
from factorlab.sources.ibkr.errors import IBKRError, IBKRPacingError, IBKRReadOnlyViolation
from factorlab.sources.ibkr.executions import pull_executions
from factorlab.sources.ibkr.historical import HistoricalBar, fetch_daily_bars
from factorlab.sources.ibkr.open_orders import snapshot_open_orders
from factorlab.sources.ibkr.pacing import RateLimiter, backoff_for
from factorlab.sources.ibkr.portfolio import snapshot_account_state, snapshot_positions
from factorlab.sources.ibkr.shapes import (
    AccountStateRow,
    ExecutionRecord,
    OpenOrderSnapshot,
    PositionSnapshot,
)

__all__ = [
    "Mode",
    "PositionSnapshot",
    "AccountStateRow",
    "ExecutionRecord",
    "OpenOrderSnapshot",
    "HistoricalBar",
    "connect",
    "disconnect",
    "connected",
    "mode_of",
    "source_channel_of",
    "ContractCache",
    "ContractKey",
    "qualify_contracts",
    "snapshot_positions",
    "snapshot_account_state",
    "pull_executions",
    "snapshot_open_orders",
    "fetch_daily_bars",
    "RateLimiter",
    "backoff_for",
    "IBKRError",
    "IBKRPacingError",
    "IBKRReadOnlyViolation",
]
