from types import SimpleNamespace
from unittest.mock import Mock

from factorlab.components.ingest_us import universe_daemon


def test_caught_universe_failure_emits_error_while_daemon_keeps_running(monkeypatch):
    monitor = Mock()
    monkeypatch.setattr(
        universe_daemon, "monitor_client", Mock(return_value=monitor), raising=False
    )
    monkeypatch.setattr(
        universe_daemon,
        "stop",
        SimpleNamespace(
            is_set=Mock(side_effect=[False, True]),
            wait=Mock(),
        ),
    )
    monkeypatch.setattr(
        universe_daemon, "sync_once", Mock(side_effect=RuntimeError("Invalid roster"))
    )
    config = SimpleNamespace(provider="github_csv", refresh_interval_minutes=1440)
    assert universe_daemon.run_daemon(config, Mock()) == 0
    monitor.error.assert_called_once()
    assert monitor.error.call_args.args[0] == "universe_refresh_failed"
    assert "Invalid roster" in monitor.error.call_args.args[1]
    assert monitor.error.call_args.kwargs["pipeline"] == "us_universe_sync"
