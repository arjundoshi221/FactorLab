"""IBKR vocabulary for the broker mirror; the record shapes live in the dataset contract.

``PositionSnapshot`` / ``AccountStateRow`` / ``ExecutionRecord`` /
``OpenOrderSnapshot`` moved to :mod:`factorlab.shared.ingest.datasets.broker`
(07 §5.5) and are re-exported here so existing imports keep working. What
stays is IBKR-only: the broker code, Gateway source channels and the
``secType`` -> ``product_type`` map.
"""

from __future__ import annotations

from typing import Literal

from factorlab.shared.ingest.datasets.broker import (
    AccountStateRow,
    ExecutionRecord,
    Mode,
    OpenOrderSnapshot,
    PositionSnapshot,
    ProductType,
    Side,
)

BrokerCode = Literal["ibkr"]
SourceChannel = Literal["paper_gateway", "live_gateway"]

# IBKR secType -> canonical product_type (matches playground/explore/ibkr/07_dual_connect.py)
SEC_TYPE_TO_PRODUCT: dict[str, ProductType] = {
    "STK": "common",
    "ETF": "etf",
    "OPT": "option",
    "FUT": "future",
    "IND": "index",
    "CASH": "forex",
    "BOND": "bond",
    "FUND": "fund",
}


def source_channel_for(mode: Mode) -> SourceChannel:
    """Return the ``source_channel`` recorded for rows captured from ``mode``'s Gateway."""
    return f"{mode}_gateway"  # type: ignore[return-value]


__all__ = [
    "SEC_TYPE_TO_PRODUCT",
    "AccountStateRow",
    "BrokerCode",
    "ExecutionRecord",
    "Mode",
    "OpenOrderSnapshot",
    "PositionSnapshot",
    "ProductType",
    "Side",
    "SourceChannel",
    "source_channel_for",
]
