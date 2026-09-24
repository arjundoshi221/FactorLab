"""Interactive Brokers read-only adapter.

Mirrors broker-side truth (positions, account state, executions, open
orders) across paper and live IBKR accounts into the ``broker.*`` mirror
tables through the shared provider contract
(:mod:`factorlab.shared.ingest.provider`):

* :mod:`.capture` — IB Gateway responses -> archivable ``RawCapture`` payloads;
* :mod:`.normalize` — pure payload -> ``broker.*`` shapes (replayable from raw);
* :mod:`.provider` — :class:`IBKRBrokerProvider`, the ingestion run per snapshot.

Order placement is out of scope — see ``docs/data-sources/us/ibkr.md`` §4
for the three-layer read-only guard.
"""

from __future__ import annotations

from factorlab.sources.ibkr.capture import (
    CapturedPayload,
    capture_account_values,
    capture_executions,
    capture_open_orders,
    capture_portfolio,
    decode_capture,
)
from factorlab.sources.ibkr.client import (
    GatewayConfig,
    Mode,
    connect,
    connect_with_retry,
    connected,
    disconnect,
    gateway_config,
    mode_of,
    source_channel_of,
)
from factorlab.sources.ibkr.contracts import ContractCache, ContractKey, qualify_contracts
from factorlab.sources.ibkr.errors import (
    IBKRCaptureError,
    IBKRConnectError,
    IBKRError,
    IBKRPacingError,
    IBKRReadOnlyViolation,
)
from factorlab.sources.ibkr.executions import pull_executions
from factorlab.sources.ibkr.historical import HistoricalBar, fetch_daily_bars
from factorlab.sources.ibkr.normalize import (
    normalize_account_state,
    normalize_capture,
    normalize_executions,
    normalize_open_orders,
    normalize_positions,
)
from factorlab.sources.ibkr.open_orders import snapshot_open_orders
from factorlab.sources.ibkr.pacing import RateLimiter, backoff_for
from factorlab.sources.ibkr.portfolio import snapshot_account_state, snapshot_positions
from factorlab.sources.ibkr.provider import (
    BrokerStorage,
    DryRunBrokerStorage,
    IBKRBrokerProvider,
    SnapshotConfig,
)
from factorlab.sources.ibkr.shapes import (
    AccountStateRow,
    ExecutionRecord,
    OpenOrderSnapshot,
    PositionSnapshot,
)

__all__ = [
    "AccountStateRow",
    "BrokerStorage",
    "CapturedPayload",
    "ContractCache",
    "ContractKey",
    "DryRunBrokerStorage",
    "ExecutionRecord",
    "GatewayConfig",
    "HistoricalBar",
    "IBKRBrokerProvider",
    "IBKRCaptureError",
    "IBKRConnectError",
    "IBKRError",
    "IBKRPacingError",
    "IBKRReadOnlyViolation",
    "Mode",
    "OpenOrderSnapshot",
    "PositionSnapshot",
    "RateLimiter",
    "SnapshotConfig",
    "backoff_for",
    "capture_account_values",
    "capture_executions",
    "capture_open_orders",
    "capture_portfolio",
    "connect",
    "connect_with_retry",
    "connected",
    "decode_capture",
    "disconnect",
    "fetch_daily_bars",
    "gateway_config",
    "mode_of",
    "normalize_account_state",
    "normalize_capture",
    "normalize_executions",
    "normalize_open_orders",
    "normalize_positions",
    "pull_executions",
    "qualify_contracts",
    "snapshot_account_state",
    "snapshot_open_orders",
    "snapshot_positions",
    "source_channel_of",
]
