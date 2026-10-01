"""Recovery and roster publication through real sources, archive and canonical sinks."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests

from factorlab.components.ingest_us import collector, universe_daemon
from factorlab.components.ingest_us.engine_client import EngineClient
from factorlab.components.ingest_us.universe import UniverseConfig
from factorlab.ingest.provider import ingestion_run
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.github_csv.sources import GithubCsvUniverse
from factorlab.sources.schwab.settings import SchwabSettings
from factorlab.sources.schwab.transport import SchwabTransport
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.v2_us import V2USStorage
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 9, 8, 21, tzinfo=UTC)


def setup(monkeypatch, *, invalid=False, include_msft=False):
    quotes = {
        "AAPL": {
            "symbol": "AAPL",
            "assetMainType": "EQUITY",
            "reference": {"exchangeName": "NASDAQ", "currency": "USD"},
        }
    }
    if include_msft:
        quotes["MSFT"] = {**quotes["AAPL"], "symbol": "MSFT"}
    candles = [
        {
            "datetime": int(datetime(2026, 9, 4, 5, tzinfo=UTC).timestamp() * 1000),
            "open": 10,
            "high": 12,
            "low": 9,
            "close": 11,
            "volume": 10,
        }
    ]
    if invalid:
        candles.append(
            {
                **candles[0],
                "datetime": int(datetime(2026, 9, 8, 5, tzinfo=UTC).timestamp() * 1000),
                "volume": -1,
            }
        )
    http = Mock(spec=requests.Session)

    def get(url, **kwargs):
        payload = quotes if url.endswith("/quotes") else {"candles": candles}
        return Mock(content=json.dumps(payload).encode(), status_code=200, headers={})

    http.get.side_effect = get
    settings = SchwabSettings()
    transport = SchwabTransport(
        settings,
        session=http,
        clock=lambda: NOW,
        secret=lambda name, _: (
            "2027-01-01T00:00:00+00:00" if name.endswith(".expires_at") else "token"
        ),
        limiter=SlidingWindowLimiter([(1, 1000)]),
    )
    client = EngineClient(transport=transport)
    db = FakeClickHouse()
    storage = V2USStorage(db)
    sink = ClickHouseSinks(storage)
    with ingestion_run(
        sink, pipeline="us_universe_sync", source="schwab", market_code="USA"
    ) as ctx:
        client.resolve_symbols(ctx, sink, ["AAPL"])
    identifier = db.rows("ref.listings")[0]["listing_id"]
    state = {
        "instrument_id": identifier,
        "history_complete": False,
        "available_from": None,
        "last_bar": None,
        "checked_through": None,
        "full_refreshed_at": None,
        "error": None,
    }
    monkeypatch.setattr(storage, "state", Mock(return_value=state))
    monkeypatch.setattr(storage, "coverage", Mock(return_value=0))
    monkeypatch.setattr(storage, "deactivate_expected_series", Mock(return_value=0))
    saved = Mock()
    monkeypatch.setattr(storage, "save_state", saved)
    return storage, client, db, identifier, http, state, saved


def test_recovery_keeps_identity_lineage_and_reports_invalid_rows(monkeypatch):
    storage, client, db, identifier, _, _, saved = setup(monkeypatch, invalid=True)
    assert collector.collect(storage, client, ("AAPL", "AAPL", identifier), "daily", now=NOW)
    state = saved.call_args.args[0]
    assert state["history_complete"] and state["checked_through"]
    assert state["error"] == "Invalid Schwab candles: 1"
    run = db.rows("meta.ingestion_runs")[-1]
    assert run["status"] == "partial"
    row = db.rows("market.bars")[0]
    assert row["listing_id"] == identifier and row["ingest_run_id"] == run["run_id"]
    assert row["raw_id"] in {r["raw_id"] for r in db.rows("raw.archive")}
    assert not collector.pending_daily(
        [{"instrument_id": identifier}], {identifier: state}, NOW.date()
    )


def test_failed_fetch_preserves_checkpoint_and_requests_overlap(monkeypatch):
    storage, client, db, identifier, http, state, saved = setup(monkeypatch)
    checkpoint = NOW - timedelta(days=4)
    state.update(
        history_complete=True,
        checked_through=checkpoint,
        full_refreshed_at=NOW - timedelta(days=1),
        available_from=checkpoint,
    )
    http.get.side_effect = requests.ConnectionError("outage")
    monkeypatch.setattr(client, "wait", lambda _: None)
    with pytest.raises(ValueError, match="No regular-session"):
        collector.collect(storage, client, ("AAPL", "AAPL", identifier), "daily", now=NOW)
    assert saved.call_args.args[0]["checked_through"] == checkpoint
    assert http.get.call_args.kwargs["params"]["startDate"] == int(
        (checkpoint - timedelta(days=7)).timestamp() * 1000
    )
    assert db.rows("meta.ingestion_runs")[-1]["status"] == "failed"


@pytest.mark.parametrize("validated", [False, True])
def test_roster_publication_requires_every_configured_listing(monkeypatch, validated):
    storage, client, db, _, _, _, _ = setup(monkeypatch, include_msft=validated)
    cfg = UniverseConfig.model_validate(
        {
            "version": 2,
            "name": "configured",
            "provider": "github_csv",
            "daily_source": "schwab",
            "refresh_interval_minutes": 1440,
            "indexes": [{"name": "test", "minimum_constituents": 2}],
            "providers": {
                "github_csv": {
                    "indexes": {
                        "test": {
                            "url": "https://raw.githubusercontent.com/example/index.csv",
                            "symbol_column": "Symbol",
                        }
                    }
                }
            },
        }
    )
    http = Mock(spec=requests.Session)
    http.get.return_value = Mock(
        url="https://raw.githubusercontent.com/example/index.csv",
        content=b"Symbol\nAAPL\nMSFT\n",
        status_code=200,
        headers={},
    )
    actual = GithubCsvUniverse
    monkeypatch.setattr(
        "factorlab.components.ingest_us.universe.engine.GithubCsvUniverse",
        lambda settings: actual(settings, session=http, clock=lambda: NOW),
    )
    publish = Mock()
    monkeypatch.setattr(storage, "sync_expected_series", publish)
    if validated:
        series = universe_daemon.sync_once(cfg, storage, client=client)
        assert {item["symbol"] for item in series} == {"AAPL", "MSFT"}
        publish.assert_called_once()
        memberships = db.rows("ref.universe_membership")
        assert {row["universe_id"] for row in memberships} == {"test", "configured"}
        raw_ids = {r["raw_id"] for r in db.rows("raw.archive")}
        assert all(row["raw_id"] in raw_ids for row in memberships)
        assert db.rows("meta.ingestion_runs")[-1]["status"] == "success"
    else:
        with pytest.raises(ValueError, match="full configured"):
            universe_daemon.sync_once(cfg, storage, client=client)
        publish.assert_not_called()
        assert not db.rows("ref.universe_membership")
        assert db.rows("meta.ingestion_runs")[-1]["status"] == "failed"


def test_pilot_aliases():
    assert ("BRK-B", "BRK/B") in collector.universe("mega_cap")
    assert len(collector.universe("mega_cap")) == 10


def test_weekly_full_daily_refresh_is_scheduled_even_when_current():
    now = datetime(2026, 9, 8, 21, tzinfo=UTC)
    ident = uuid4()
    from factorlab.calendars.us import bounds

    session_bounds = bounds(now.date())
    assert session_bounds is not None
    state = {
        "history_complete": True,
        "checked_through": session_bounds[1],
        "full_refreshed_at": now - timedelta(days=8),
        "error": None,
    }
    assert collector.pending_daily([{"instrument_id": ident}], {ident: state}, now.date())
