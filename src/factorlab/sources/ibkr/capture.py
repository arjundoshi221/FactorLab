"""Capture IB Gateway responses as archivable, replayable payloads.

IBKR is a TCP-socket API, so there is no HTTP body to archive. Each capture
serializes what ``ib_async`` returned — every field of every object, not a
curated subset — into a deterministic JSON envelope::

    {"schema": "ibkr.portfolio.v1", "kind": "portfolio", "mode": "paper",
     "fetched_at": "<UTC ISO>", "request": {...}, "data": [...]}

Normalization (:mod:`factorlab.sources.ibkr.normalize`) reads only these
bytes, so a row in ``broker.*`` can always be reproduced from its
``raw.archive`` record. Floats are kept as JSON numbers (``NaN`` included),
``Decimal`` as strings and datetimes as ISO strings.

Read-only: these functions only call IBKR accessor/request methods.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from factorlab.shared.ingest.provider import RawCapture
from factorlab.sources.ibkr.client import mode_of
from factorlab.sources.ibkr.errors import IBKRCaptureError
from factorlab.sources.ibkr.shapes import Mode

if TYPE_CHECKING:
    from ib_async import IB

CAPTURE_VERSION = 1
CONTENT_TYPE = "application/json"
TRANSPORT = "tcp_socket"
# ExecutionFilter.time in IBKR's explicit-UTC form ("yyyymmdd-hh:mm:ss").
EXECUTION_FILTER_TIME_FMT = "%Y%m%d-%H:%M:%S"

CaptureKind = Literal["portfolio", "account_values", "executions", "open_orders"]
CAPTURE_KINDS: tuple[CaptureKind, ...] = ("portfolio", "account_values", "executions", "open_orders")


def to_jsonable(value: Any) -> Any:
    """Recursively convert ``ib_async`` objects into JSON-compatible values."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, "_asdict"):  # NamedTuple (PortfolioItem, AccountValue, Fill, ...)
        return {str(k): to_jsonable(v) for k, v in value._asdict().items()}
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted((to_jsonable(v) for v in value), key=repr)
    if hasattr(value, "__dict__"):
        return {str(k): to_jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _utc(value: datetime | None) -> datetime:
    stamp = value if value is not None else datetime.now(UTC)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("capture timestamps must be UTC-aware")
    return stamp.astimezone(UTC)


def _capture(kind: CaptureKind, mode: Mode, fetched_at: datetime, data: list[Any],
             request: Mapping[str, Any] | None = None) -> RawCapture:
    envelope = {
        "schema": f"ibkr.{kind}.v{CAPTURE_VERSION}",
        "kind": kind,
        "mode": mode,
        "fetched_at": fetched_at.isoformat(),
        "request": dict(request or {}),
        "data": data,
    }
    body = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return RawCapture(
        body=body, request_key=f"{kind}:{mode}", transport=TRANSPORT, fetched_at=fetched_at,
        source_url=f"ibkr-gateway://{mode}/{kind}", content_type=CONTENT_TYPE,
        metadata={"schema": envelope["schema"], "records": len(data)},
    )


def capture_portfolio(ib: IB, *, fetched_at: datetime | None = None) -> RawCapture:
    """``ib.portfolio()`` — positions with market price, value and PnL per account."""
    stamp = _utc(fetched_at)
    return _capture("portfolio", mode_of(ib), stamp, [to_jsonable(i) for i in ib.portfolio()])


def capture_account_values(ib: IB, *, fetched_at: datetime | None = None) -> RawCapture:
    """``ib.accountValues()`` — every account tag/segment/currency the Gateway reports."""
    stamp = _utc(fetched_at)
    return _capture("account_values", mode_of(ib), stamp,
                    [to_jsonable(v) for v in ib.accountValues()])


def capture_executions(ib: IB, *, since: datetime | None = None,
                       fetched_at: datetime | None = None) -> RawCapture:
    """``ib.reqExecutions()`` fills; ``since`` narrows via ``ExecutionFilter(time=...)``."""
    stamp = _utc(fetched_at)
    request: dict[str, Any] = {}
    if since is None:
        fills = ib.reqExecutions()
    else:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("since must be UTC-aware")
        from ib_async import ExecutionFilter

        request["since"] = since.astimezone(UTC).strftime(EXECUTION_FILTER_TIME_FMT)
        fills = ib.reqExecutions(ExecutionFilter(time=request["since"]))
    data = [{
        "contract": to_jsonable(getattr(fill, "contract", None)),
        "execution": to_jsonable(getattr(fill, "execution", None)),
        "commissionReport": to_jsonable(getattr(fill, "commissionReport", None)),
        "time": to_jsonable(getattr(fill, "time", None)),
    } for fill in fills]
    return _capture("executions", mode_of(ib), stamp, data, request)


def capture_open_orders(ib: IB, *, fetched_at: datetime | None = None) -> RawCapture:
    """``ib.openTrades()`` — contract, order parameters and status (observe only)."""
    stamp = _utc(fetched_at)
    data = [{
        "contract": to_jsonable(getattr(trade, "contract", None)),
        "order": to_jsonable(getattr(trade, "order", None)),
        "orderStatus": to_jsonable(getattr(trade, "orderStatus", None)),
    } for trade in ib.openTrades()]
    return _capture("open_orders", mode_of(ib), stamp, data)


@dataclass(frozen=True, slots=True)
class CapturedPayload:
    """A decoded capture envelope, the only input normalization accepts."""

    kind: CaptureKind
    mode: Mode
    fetched_at: datetime
    request: Mapping[str, Any]
    data: list[Any]


def decode_capture(body: bytes, *, expected_kind: CaptureKind | None = None) -> CapturedPayload:
    """Parse and validate archived capture bytes."""
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IBKRCaptureError(f"IBKR capture is not valid JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise IBKRCaptureError("IBKR capture must be a JSON object")
    kind = envelope.get("kind")
    if kind not in CAPTURE_KINDS or envelope.get("schema") != f"ibkr.{kind}.v{CAPTURE_VERSION}":
        raise IBKRCaptureError(f"Unsupported IBKR capture schema: {envelope.get('schema')!r}")
    if expected_kind is not None and kind != expected_kind:
        raise IBKRCaptureError(f"Expected {expected_kind} capture, got {kind}")
    mode = envelope.get("mode")
    if mode not in ("paper", "live"):
        raise IBKRCaptureError(f"IBKR capture has invalid mode {mode!r}")
    data = envelope.get("data")
    if not isinstance(data, list):
        raise IBKRCaptureError("IBKR capture data must be a list")
    try:
        fetched_at = _utc(datetime.fromisoformat(str(envelope.get("fetched_at"))))
    except ValueError as exc:
        raise IBKRCaptureError(f"IBKR capture has invalid fetched_at: {exc}") from exc
    request = envelope.get("request")
    return CapturedPayload(kind=kind, mode=mode, fetched_at=fetched_at,
                           request=request if isinstance(request, dict) else {}, data=data)
