"""Capture -> archive bytes -> normalize: determinism, replay and sentinel handling."""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from factorlab.sources.ibkr.capture import (
    capture_account_values,
    capture_executions,
    capture_open_orders,
    capture_portfolio,
    decode_capture,
    to_jsonable,
)
from factorlab.sources.ibkr.errors import IBKRCaptureError
from factorlab.sources.ibkr.normalize import normalize_capture, normalize_positions, to_decimal

from .conftest import (
    make_account_value,
    make_contract,
    make_fill,
    make_portfolio_item,
    make_trade,
)


def _loaded(mock_ib):
    mock_ib.portfolio.return_value = [
        make_portfolio_item(),
        make_portfolio_item(contract=make_contract(symbol="SPY", con_id=756733), position=-5.0),
    ]
    mock_ib.accountValues.return_value = [make_account_value(), make_account_value(tag="Cushion-S",
                                                                                  value="0.9")]
    mock_ib.reqExecutions.return_value = [make_fill()]
    mock_ib.openTrades.return_value = [make_trade()]
    return mock_ib


@pytest.mark.parametrize("capture", [
    capture_portfolio, capture_account_values, capture_executions, capture_open_orders,
])
def test_capture_is_deterministic_and_replay_matches(mock_ib_paper, now_utc, capture):
    ib = _loaded(mock_ib_paper)
    first = capture(ib, fetched_at=now_utc)
    second = capture(ib, fetched_at=now_utc)
    assert first.body == second.body
    assert first.transport == "tcp_socket"
    assert first.request_key.endswith(":paper")
    assert first.metadata["records"] >= 1
    # Replaying the archived bytes twice yields identical rows (idempotent ingest).
    assert normalize_capture(first.body) == normalize_capture(second.body)
    assert normalize_capture(first.body)


def test_capture_envelope_shape(mock_ib_live, now_utc):
    capture = capture_portfolio(_loaded(mock_ib_live), fetched_at=now_utc)
    envelope = json.loads(capture.body)
    assert envelope["schema"] == "ibkr.portfolio.v1"
    assert envelope["mode"] == "live"
    assert envelope["fetched_at"] == now_utc.isoformat()
    assert envelope["data"][0]["contract"]["conId"] == 265598
    assert capture.source_url == "ibkr-gateway://live/portfolio"


def test_executions_capture_records_utc_filter(mock_ib_paper, now_utc):
    since = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
    capture = capture_executions(mock_ib_paper, since=since, fetched_at=now_utc)
    assert json.loads(capture.body)["request"] == {"since": "20260918-04:00:00"}
    assert mock_ib_paper.reqExecutions.call_args.args[0].time == "20260918-04:00:00"


def test_capture_serializes_real_ib_async_types(now_utc):
    ib_async = pytest.importorskip("ib_async")
    contract = ib_async.Stock("AAPL", "SMART", "USD")
    item = ib_async.PortfolioItem(contract, 10.0, 1.0, 2.0, 3.0, 4.0, 5.0, "DU1")
    data = to_jsonable(item)
    assert data["contract"]["symbol"] == "AAPL"
    assert data["position"] == 10.0
    assert data["account"] == "DU1"
    value = to_jsonable(ib_async.AccountValue("DU1", "NetLiquidation", "100", "USD", ""))
    assert value == {"account": "DU1", "tag": "NetLiquidation", "value": "100",
                     "currency": "USD", "modelCode": ""}


def test_sentinels_and_non_finite_become_none():
    assert to_decimal(sys.float_info.max) is None  # IBKR UNSET_DOUBLE
    assert to_decimal(str(2 ** 127 - 1)) is None  # IBKR UNSET_DECIMAL
    assert to_decimal(math.nan) is None
    assert to_decimal("") is None
    assert to_decimal(True) is None
    assert to_decimal("1.25") == Decimal("1.25")


def test_nan_market_price_survives_archive_as_null(mock_ib_paper, now_utc):
    mock_ib_paper.portfolio.return_value = [make_portfolio_item(market_price=math.nan)]
    capture = capture_portfolio(mock_ib_paper, fetched_at=now_utc)
    [row] = normalize_positions(decode_capture(capture.body))
    assert row.market_price is None


@pytest.mark.parametrize("body,message", [
    (b"not json", "not valid JSON"),
    (b"[]", "JSON object"),
    (b'{"schema":"ibkr.portfolio.v9","kind":"portfolio"}', "Unsupported"),
    (b'{"schema":"ibkr.portfolio.v1","kind":"portfolio","mode":"demo","data":[]}', "mode"),
    (b'{"schema":"ibkr.portfolio.v1","kind":"portfolio","mode":"paper","data":{}}', "list"),
    ((b'{"schema":"ibkr.portfolio.v1","kind":"portfolio","mode":"paper","data":[],'
      b'"fetched_at":"2026-09-19T20:30:00"}'), "UTC-aware"),
])
def test_decode_rejects_bad_payloads(body, message):
    with pytest.raises((IBKRCaptureError, ValueError), match=message):
        decode_capture(body)


def test_decode_enforces_expected_kind(mock_ib_paper, now_utc):
    capture = capture_open_orders(mock_ib_paper, fetched_at=now_utc)
    with pytest.raises(IBKRCaptureError, match="Expected portfolio"):
        decode_capture(capture.body, expected_kind="portfolio")


def test_order_ref_and_manual_client_id_are_kept(mock_ib_paper, now_utc):
    fill = make_fill()
    fill.execution.orderRef = "rebalance-42"
    fill.execution.clientId = 0
    mock_ib_paper.reqExecutions.return_value = [fill]
    [row] = normalize_capture(capture_executions(mock_ib_paper, fetched_at=now_utc).body)
    assert row.order_ref == "rebalance-42"
    assert row.placed_by_client == 0
    assert row.route_pref == ""


def test_ledger_tags_stay_distinct_metrics(mock_ib_live, now_utc):
    """Real live payload shape: $LEDGER-* tags must not collapse into one metric."""
    mock_ib_live.accountValues.return_value = [
        make_account_value(account="U1", tag="$LEDGER-CashBalance", value="10", currency="USD"),
        make_account_value(account="U1", tag="$LEDGER-NetLiquidationByCurrency", value="20",
                           currency="USD"),
        make_account_value(account="U1", tag="$LEDGER-CashBalance", value="30", currency="BASE"),
        make_account_value(account="U1", tag="NetLiquidation-S", value="40", currency="USD"),
        make_account_value(account="U1", tag="NetLiquidation", value="50", currency="USD"),
    ]
    rows = normalize_capture(capture_account_values(mock_ib_live, fetched_at=now_utc).body)
    keys = [(r.metric, r.segment, r.currency) for r in rows]
    assert len(set(keys)) == len(rows) == 5
    assert ("$LEDGER-CashBalance", "", "USD") in keys
    assert ("$LEDGER-NetLiquidationByCurrency", "", "USD") in keys
    assert ("NetLiquidation", "S", "USD") in keys
    assert ("NetLiquidation", "", "USD") in keys
