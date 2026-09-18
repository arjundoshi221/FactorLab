from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from factorlab.sources.schwab import market


def bar(stamp, **kwargs):
    return {"datetime": int(datetime.fromisoformat(stamp).timestamp() * 1000),
            "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, **kwargs}


@pytest.mark.parametrize("day,opened,closed", [
    (date(2026, 3, 6), 14, 21), (date(2026, 3, 9), 13, 20),
    (date(2026, 11, 27), 14, 18),
])
def test_dst_and_early_close(day, opened, closed):
    start, end = market.bounds(day)
    assert (start.hour, start.minute, end.hour) == (opened, 30, closed)


def test_holiday_and_postclose_grace():
    assert market.bounds(date(2026, 9, 7)) is None
    assert market.latest_completed(datetime(2026, 9, 8, 20, 29, tzinfo=UTC)) == date(2026, 9, 4)
    assert market.latest_completed(datetime(2026, 9, 8, 20, 30, tzinfo=UTC)) == date(2026, 9, 8)


def test_minute_filters_extended_hours_incomplete_and_duplicates():
    frame = market.normalize([
        bar("2026-09-04T13:29:00+00:00"), bar("2026-09-04T13:30:00+00:00"),
        bar("2026-09-04T13:30:00+00:00", close=10), bar("2026-09-04T13:31:00+00:00"),
        bar("2026-09-04T20:00:00+00:00"),
    ], "1min", now=datetime(2026, 9, 4, 13, 31, 30, tzinfo=UTC))
    assert len(frame) == 1
    assert frame.iloc[0]["close"] == 10


def test_daily_uses_new_york_date_and_excludes_incomplete_session():
    frame = market.normalize([bar("2026-09-04T05:00:00+00:00"), bar("2026-09-08T05:00:00+00:00")],
                             "daily", now=datetime(2026, 9, 8, 15, tzinfo=UTC))
    assert list(frame.trade_date) == [date(2026, 9, 4)]
    assert "adj_close" not in frame


@pytest.mark.parametrize("update", [{"high": 8}, {"volume": -1}, {"volume": 1.2}, {"close": float("nan")}])
def test_invalid_ohlcv_rejected(update):
    with pytest.raises(ValueError):
        market.normalize([bar("2026-09-04T13:30:00+00:00", **update)], "1min",
                         now=datetime(2026, 9, 4, 14, tzinfo=UTC))


def test_missing_and_expired_credentials_pause(monkeypatch):
    values = {"SCHWAB_ACCESS_TOKEN": "example", "SCHWAB_ACCESS_TOKEN.expires_at": "2026-09-04T14:00:00Z"}
    monkeypatch.setattr(market, "get_secret", lambda name, default: values.get(name, default))
    assert market.token_ready(datetime(2026, 9, 4, 13, tzinfo=UTC))
    assert not market.token_ready(datetime(2026, 9, 4, 15, tzinfo=UTC))
    del values["SCHWAB_ACCESS_TOKEN"]
    assert not market.token_ready(datetime(2026, 9, 4, 13, tzinfo=UTC))


def response(code, payload=None, headers=None):
    return SimpleNamespace(status_code=code, ok=code == 200, headers=headers or {},
                           content=b"{}", json=lambda: payload or {"candles": []})


def test_throttle_retry_after_and_raw_archive(monkeypatch):
    monkeypatch.setattr(market, "token_ready", lambda: True)
    session, storage, sleep = Mock(), Mock(), Mock()
    session.get.side_effect = [response(429, headers={"Retry-After": "3"}), response(200)]
    client = market.MarketClient(storage, session=session, sleep=sleep)
    client.get("/pricehistory", {"symbol": "AAPL"})
    assert session.get.call_count == 2
    assert storage.archive_http_response.call_count == 2
    assert any(call.args == (3.0,) for call in sleep.call_args_list)
    assert session.get.call_args.kwargs["timeout"] == 30


def test_token_removal_stops_before_next_request(monkeypatch):
    monkeypatch.setattr(market, "token_ready", lambda: False)
    session = Mock()
    with pytest.raises(market.AuthRequired):
        market.MarketClient(Mock(), session=session).get("/pricehistory", {})
    session.get.assert_not_called()


@pytest.mark.parametrize("failure", [response(500), requests.Timeout()])
def test_retries_are_bounded(monkeypatch, failure):
    monkeypatch.setattr(market, "token_ready", lambda: True)
    session = Mock()
    if isinstance(failure, Exception):
        session.get.side_effect = failure
    else:
        session.get.return_value = failure
    with pytest.raises(RuntimeError):
        market.MarketClient(Mock(), session=session, sleep=lambda _: None).get("/pricehistory", {})
    assert session.get.call_count == 5


def test_reference_requires_exact_symbol_and_handles_berkshire():
    client = market.MarketClient(Mock())
    client.get = Mock(return_value=({"instruments": [{"symbol": "BRK/B", "assetType": "EQUITY"}]}, "raw"))
    assert client.instrument("BRK/B")[0]["symbol"] == "BRK/B"
    with pytest.raises(ValueError):
        client.instrument("BRK-B")
