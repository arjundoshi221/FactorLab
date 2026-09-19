"""Tests for executions.pull_executions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from factorlab.sources.ibkr.executions import (
    _normalize_exec_time,
    _normalize_side,
    pull_executions,
)

from .conftest import (
    make_commission_report,
    make_contract,
    make_execution,
    make_fill,
)


def test_pull_executions_maps_fill(mock_ib_paper, now_utc):
    fill = make_fill(
        contract=make_contract(symbol="AAPL", con_id=265598),
        execution=make_execution(
            exec_id="abc.001", side="BOT", shares=100, price=175.32,
            time_=datetime(2026, 9, 19, 13, 45, tzinfo=UTC), last_liquidity=2,
        ),
        commission_report=make_commission_report(commission=1.05, realized_pnl=25.5),
    )
    mock_ib_paper.reqExecutions.return_value = [fill]

    rows = pull_executions(mock_ib_paper, now=now_utc)
    assert len(rows) == 1
    r = rows[0]
    assert r.exec_id == "abc.001"
    assert r.side == "BUY"
    assert r.quantity == Decimal("100")
    assert r.price == Decimal("175.32")
    assert r.commission == Decimal("1.05")
    assert r.commission_ccy == "USD"
    assert r.realized_pnl == Decimal("25.5")
    assert r.liquidity_flag == "REMOVED"
    assert r.exec_time == datetime(2026, 9, 19, 13, 45, tzinfo=UTC)
    assert r.vendor_id == "265598"
    assert r.trading_symbol == "AAPL"
    assert r.account_mode == "paper"
    assert r.source_channel == "paper_gateway"


def test_pull_executions_idempotent_by_exec_id(mock_ib_paper, now_utc):
    fill_a = make_fill(execution=make_execution(exec_id="dup.001"))
    fill_b = make_fill(execution=make_execution(exec_id="dup.001"))
    mock_ib_paper.reqExecutions.return_value = [fill_a, fill_b]
    rows = pull_executions(mock_ib_paper, now=now_utc)
    # The adapter emits both rows — dedup happens in the sink (ReplacingMergeTree
    # PK=exec_id). Prove that both share the exec_id so the sink can collapse.
    assert [r.exec_id for r in rows] == ["dup.001", "dup.001"]


def test_pull_executions_since_uses_filter(mock_ib_paper, now_utc):
    since = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)
    mock_ib_paper.reqExecutions.return_value = []
    pull_executions(mock_ib_paper, since=since, now=now_utc)
    assert mock_ib_paper.reqExecutions.called
    call_args = mock_ib_paper.reqExecutions.call_args
    # It should have been called with an ExecutionFilter (any object)
    assert len(call_args.args) == 1
    filt = call_args.args[0]
    assert getattr(filt, "time", "").startswith("20260918")


def test_pull_executions_no_since_uses_default(mock_ib_paper, now_utc):
    mock_ib_paper.reqExecutions.return_value = []
    pull_executions(mock_ib_paper, now=now_utc)
    mock_ib_paper.reqExecutions.assert_called_once_with()


def test_normalize_side():
    assert _normalize_side("BOT") == "BUY"
    assert _normalize_side("SLD") == "SELL"
    assert _normalize_side("SSHORT") == "SSHORT"
    assert _normalize_side("BUY") == "BUY"
    assert _normalize_side(None) == "BUY"


def test_normalize_exec_time_from_string():
    parsed = _normalize_exec_time("20260919 13:45:00")
    assert parsed == datetime(2026, 9, 19, 13, 45, tzinfo=UTC)


def test_normalize_exec_time_from_naive_datetime():
    parsed = _normalize_exec_time(datetime(2026, 9, 19, 13, 45))
    assert parsed.tzinfo is not None


def test_pull_executions_since_must_be_aware(mock_ib_paper, now_utc):
    with pytest.raises(ValueError):
        pull_executions(mock_ib_paper, since=datetime(2026, 9, 18), now=now_utc)


def test_pull_executions_now_must_be_aware(mock_ib_paper):
    with pytest.raises(ValueError):
        pull_executions(mock_ib_paper, now=datetime(2026, 9, 19))


def test_pull_executions_missing_commission_report(mock_ib_paper, now_utc):
    fill = make_fill(commission_report=None)
    mock_ib_paper.reqExecutions.return_value = [fill]
    rows = pull_executions(mock_ib_paper, now=now_utc)
    assert rows[0].commission is None
    assert rows[0].realized_pnl is None
    assert rows[0].commission_ccy == ""


def test_pull_executions_uses_live_channel(mock_ib_live, now_utc):
    mock_ib_live.reqExecutions.return_value = [make_fill()]
    rows = pull_executions(mock_ib_live, now=now_utc)
    assert rows[0].source_channel == "live_gateway"
    assert rows[0].account_mode == "live"
