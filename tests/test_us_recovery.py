import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from factorlab.sources.schwab.market import normalize
from factorlab.storage.clickhouse import instrument_id_for

spec = importlib.util.spec_from_file_location("us_runner", Path(__file__).parents[1] / "scripts/factlab_us_clickhouse.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_pilot_aliases():
    assert ("BRK-B", "BRK/B") in runner.universe("mega_cap")
    assert len(runner.universe("mega_cap")) == 10


def test_resume_uses_checkpoint_overlap_and_does_not_advance_on_failure():
    now = datetime(2026, 9, 8, 21, tzinfo=UTC)
    ident = instrument_id_for("schwab:USA:AAPL")
    state = {"instrument_id": ident, "history_complete": True,
             "available_from": datetime(1985, 1, 2, 5, tzinfo=UTC), "last_bar": now - timedelta(days=4),
             "checked_through": now - timedelta(days=4), "full_refreshed_at": now - timedelta(days=1), "error": None}
    storage = Mock()
    storage.coverage.return_value = 0
    storage.state.return_value = state.copy()
    client = Mock()
    client.candles.side_effect = RuntimeError("temporary outage")
    with pytest.raises(RuntimeError):
        runner.collect(storage, client, ("AAPL", "AAPL", ident), "daily", now=now)
    assert client.candles.call_args.args[2] == state["checked_through"] - timedelta(days=7)
    saved = storage.save_state.call_args.args[0]
    assert saved["checked_through"] == state["checked_through"]
    assert saved["error"] == "temporary outage"
    assert storage.finish_ingestion_run.call_args.kwargs["status"] == "failed"


def test_checkpoint_is_written_only_after_candles_and_coverage():
    now = datetime(2026, 9, 6, 21, tzinfo=UTC)
    ident = instrument_id_for("schwab:USA:AAPL")
    storage = Mock()
    storage.coverage.return_value = 0
    storage.state.return_value = {"instrument_id": ident, "history_complete": False, "available_from": None,
                                  "last_bar": None, "checked_through": None, "full_refreshed_at": None}
    storage.write_daily.return_value = 1
    client = Mock()
    client.candles.return_value = (normalize([{"datetime": 1788498000000, "open": 10, "high": 12,
        "low": 9, "close": 11, "volume": 10}], "daily", now=now), "raw")
    runner.collect(storage, client, ("AAPL", "AAPL", ident), "daily", now=now)
    names = [call[0] for call in storage.mock_calls]
    assert names.index("write_daily") < names.index("coverage") < names.index("save_state")
    assert storage.save_state.call_args.args[0]["history_complete"] is True
    storage.reset_mock()
    storage.coverage.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        runner.collect(storage, client, ("AAPL", "AAPL", ident), "daily", now=now)
    assert storage.finish_ingestion_run.call_args.kwargs["status"] == "failed"
