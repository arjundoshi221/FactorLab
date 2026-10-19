from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from factorlab.components.ingest_us import collector as runner


def harness(monkeypatch, *, iterations=1, complete=True):
    # Pin a regular session with an overdue live sweep, daily backlog and minute recovery.
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 15, tzinfo=UTC)

    monkeypatch.setattr(runner, "datetime", Clock)
    stop = Mock()
    stop.is_set.side_effect = [False] * iterations + [True]
    monkeypatch.setattr(runner, "stop", stop)
    items = [
        {"instrument_id": uuid4(), "symbol": symbol, "provider_symbol": symbol}
        for symbol in ("AAPL", "MSFT")
    ]
    storage = Mock()
    storage.active_expected_series.return_value = items
    storage.states.return_value = {}
    storage.unresolved_series.return_value = 0
    monkeypatch.setattr(
        runner,
        "select_minute_tier",
        Mock(
            return_value=[
                (item["symbol"], item["provider_symbol"], item["instrument_id"]) for item in items
            ]
        ),
    )
    collect = Mock(return_value=complete)
    monkeypatch.setattr(runner, "collect", collect)
    return storage, collect, items


def test_daily_backlog_preempts_live_sweeps_and_minute_history(monkeypatch):
    storage, collect, items = harness(monkeypatch, iterations=2)
    runner.run_full(
        SimpleNamespace(daemon=True), storage, Mock(token_ready=Mock(return_value=True))
    )
    assert [call.args[3] for call in collect.call_args_list] == ["daily", "daily"]
    assert [call.args[2][2] for call in collect.call_args_list] == [
        item["instrument_id"] for item in items
    ]


def test_partial_daily_result_is_retained_in_recovery_queue(monkeypatch):
    storage, collect, items = harness(monkeypatch, complete=False)
    status = Mock(return_value=("recovering", "pending"))
    monkeypatch.setattr(runner, "collection_status", status)
    retry = Mock(wraps=runner.schedule_retry)
    monkeypatch.setattr(runner, "schedule_retry", retry)
    runner.run_full(
        SimpleNamespace(daemon=True), storage, Mock(token_ready=Mock(return_value=True))
    )
    assert collect.call_args.args[3] == "daily"
    retry.assert_called_once()
    assert retry.call_args.args[2] == items[0]["instrument_id"]
    assert len(status.call_args.args[1]) == 2


def test_zero_minute_tier_keeps_daily_membership_and_deactivates_minutes(monkeypatch):
    monkeypatch.setattr(runner, "MINUTE_TIER_SIZE", 0)
    storage = Mock()
    assert runner.select_minute_tier(storage, Mock(), [{"instrument_id": uuid4()}]) == []
    storage.liquid_candidates.assert_not_called()
    assert storage.sync_expected_series.call_args.args[0] == []
    assert storage.sync_expected_series.call_args.kwargs["resolution"] == "1min"
