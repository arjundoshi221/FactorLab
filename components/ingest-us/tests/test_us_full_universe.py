from unittest.mock import Mock
from uuid import uuid4

from factorlab.components.ingest_us import collector as runner
from factorlab.sources.schwab.normalize import canonical_symbol
from factorlab.sources.schwab.normalize import provider_symbol as schwab_symbol


def test_symbol_mapping():
    assert canonical_symbol("brk/b") == "BRK-B"
    assert schwab_symbol("BRK-B") == "BRK/B"


def test_pending_daily_includes_incomplete_stale_and_error_states():
    now = runner.datetime(2026, 9, 15, 22, tzinfo=runner.UTC)
    target_day = runner.latest_completed(now)
    target = runner.bounds(target_day)[1]
    identifiers = [uuid4() for _ in range(4)]
    items = [
        {"instrument_id": identifier, "symbol": str(index)}
        for index, identifier in enumerate(identifiers)
    ]
    states = {
        identifiers[0]: {"history_complete": False, "checked_through": None, "error": None},
        identifiers[1]: {
            "history_complete": True,
            "checked_through": target - runner.timedelta(days=1),
            "error": None,
        },
        identifiers[2]: {"history_complete": True, "checked_through": target, "error": "retry"},
        identifiers[3]: {
            "history_complete": True,
            "checked_through": target,
            "full_refreshed_at": now,
            "error": None,
        },
    }
    assert [
        item["instrument_id"] for item in runner.pending_daily(items, states, target_day)
    ] == identifiers[:3]


def test_schwab_health_stays_incomplete_after_daily_queue_drains():
    storage = Mock()
    storage.unresolved_series.return_value = 3
    assert runner.collection_status(storage, [], [{}] * 503) == (
        "incomplete",
        "3 series have unresolved errors or gaps",
    )
    assert runner.collection_status(storage, [{}], [{}] * 503) == (
        "recovering",
        "1 daily histories pending",
    )
    storage.unresolved_series.return_value = 0
    assert runner.collection_status(storage, [], [{}] * 503) == ("ready", "503 configured equities")


def test_liquid_tier_uses_resolver_validated_symbols(monkeypatch):
    monkeypatch.setattr(runner, "MINUTE_TIER_SIZE", 2)
    identifiers = [uuid4() for _ in range(3)]
    storage = Mock()
    storage.liquid_candidates.return_value = [
        {"instrument_id": identifier, "symbol": symbol}
        for identifier, symbol in zip(identifiers, ["BAD", "AAPL", "MSFT"], strict=True)
    ]
    client = Mock()
    items = [
        {"instrument_id": identifier, "symbol": symbol, "provider_symbol": f"{symbol}.US"}
        for identifier, symbol in zip(identifiers, ["BAD", "AAPL", "MSFT"], strict=True)
    ]
    result = runner.select_minute_tier(storage, client, items)
    assert [item[0] for item in result] == ["BAD", "AAPL"]
    client.instrument.assert_not_called()
    assert storage.sync_expected_series.call_args.kwargs == {
        "source": "schwab",
        "universe": "us_liquid_250",
        "resolution": "1min",
    }


def test_liquid_tier_targets_configured_count_when_below_cap(monkeypatch):
    monkeypatch.setattr(runner, "MINUTE_TIER_SIZE", 250)
    identifiers = [uuid4() for _ in range(2)]
    items = [
        {"instrument_id": identifier, "symbol": symbol, "provider_symbol": f"{symbol}.US"}
        for identifier, symbol in zip(identifiers, ["AAPL", "MSFT"], strict=True)
    ]
    storage = Mock()
    storage.liquid_candidates.return_value = items
    client = Mock()
    result = runner.select_minute_tier(storage, client, items)
    assert len(result) == 2
    storage.liquid_candidates.assert_called_once_with(limit=152)


def test_recovery_backoff_doubles_to_daily_cap():
    minutes = [runner.retry_delay(n).total_seconds() / 60 for n in range(9)]
    assert minutes == [15, 30, 60, 120, 240, 480, 960, 1440, 1440]
    now = runner.datetime(2026, 9, 25, 16, tzinfo=runner.UTC)
    retry_at, attempts = {}, {}
    runner.schedule_retry(retry_at, attempts, "id", now)
    runner.schedule_retry(retry_at, attempts, "id", now)
    assert attempts == {"id": 2}
    assert retry_at["id"] == now + runner.timedelta(minutes=30)
