"""Futures roster and quotes through the real Schwab sources, archive and canonical sinks."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import Mock

import requests

from factorlab.calendars.us import NY
from factorlab.components.ingest_us.engine_client import EngineClient
from factorlab.components.ingest_us.futures import RosterSink
from factorlab.ingest.provider import ingestion_run
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.sources.schwab.futures import FuturesRoot
from factorlab.sources.schwab.settings import SchwabSettings
from factorlab.sources.schwab.transport import SchwabTransport
from factorlab.storage.v2_us import V2USStorage
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)  # 19:00 New York: Tuesday's session
ES = FuturesRoot("/ES", "XCME", "index_future", "HMUZ", "E-mini S&P 500")
QUOTED = int(datetime(2026, 10, 19, 22, 59, 30, tzinfo=UTC).timestamp() * 1000)


def expiring(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY).timestamp() * 1000)


EXPIRIES = {
    "/ESZ26": date(2026, 12, 18),
    "/ESH27": date(2027, 3, 19),
    "/ESM27": date(2027, 6, 18),
    "/ESU27": date(2027, 9, 17),
}


def item(symbol: str) -> dict:
    # Schwab's shape (production, 2026-10-21): every contract names its root in
    # `product`; only the active one has futureIsActive.
    reference = {
        "product": "/ES",
        "futureIsActive": symbol == "/ESZ26",
        "futureMultiplier": 50,
        "futureSettlementPrice": 5795.5,
        "futureExpirationDate": expiring(EXPIRIES[symbol]),
    }
    return {
        "assetMainType": "FUTURE",
        "symbol": symbol,
        "quote": {
            "tick": 0.25,
            "lastPrice": 5801.25,
            "bidPrice": 5801,
            "askPrice": 5801.5,
            "openPrice": 5799,
            "highPrice": 5802,
            "lowPrice": 5798.5,
            "totalVolume": 41000,
            "openInterest": 2000000,
            "quoteTime": QUOTED,
        },
        "reference": reference,
    }


def client_and_sink():
    http = Mock(spec=requests.Session)

    def get(url, **kwargs):
        assert url.endswith("/marketdata/v1/quotes")
        symbols = kwargs["params"]["symbols"].split(",")
        # A root answers with its active contract, keyed by the contract symbol.
        wanted = {"/ESZ26" if s == "/ES" else s for s in symbols}
        payload = {s: item(s) for s in wanted if s in EXPIRIES}
        return Mock(content=json.dumps(payload).encode(), status_code=200, headers={})

    http.get.side_effect = get
    transport = SchwabTransport(
        SchwabSettings(),
        session=http,
        clock=lambda: NOW,
        secret=lambda name, _: (
            "2027-01-01T00:00:00+00:00" if name.endswith(".expires_at") else "token"
        ),
        limiter=SlidingWindowLimiter([(1, 1000)]),
    )
    db = FakeClickHouse({"XCME": ("XCME", "US", "USD")})
    return EngineClient(transport=transport), RosterSink(V2USStorage(db)), db, http


def test_roster_mints_root_and_contracts_then_quotes_land_with_lineage():
    client, sink, db, http = client_and_sink()
    with ingestion_run(
        sink, pipeline="us_futures_roster", source="schwab", market_code="USA"
    ) as ctx:
        client.collect_futures_roster(ctx, sink, [ES.to_mapping()], NOW.astimezone(NY).date())
    assert sink.roster == {"/ESZ26": "XCME", "/ESH27": "XCME"}
    [root] = db.rows("ref.listings")
    [security] = db.rows("ref.securities")
    assert (root["trading_symbol"], root["mic"]) == ("/ES", "XCME")
    assert security["security_type"] == "index_future"
    contracts = {c["expiry"]: c for c in db.rows("ref.contracts")}
    assert sorted(contracts) == [date(2026, 12, 18), date(2027, 3, 19)]
    assert {c["underlying_listing_id"] for c in contracts.values()} == {root["listing_id"]}
    assert contracts[date(2026, 12, 18)]["multiplier"] == 50

    with ingestion_run(
        sink, pipeline="us_futures_quotes", source="schwab", market_code="USA"
    ) as ctx:
        client.collect_futures_quotes(ctx, sink, sink.roster)
    quotes = db.rows("market.futures_quotes")
    assert sorted(q["source_symbol"] for q in quotes) == ["/ESH27", "/ESZ26"]
    run = db.rows("meta.ingestion_runs")[-1]
    archived = {r["raw_id"] for r in db.rows("raw.archive")}
    for quote in quotes:
        assert quote["trade_date"] == date(2026, 10, 20)
        assert quote["last_price"] == Decimal("5801.25")
        assert quote["settlement_price"] == Decimal("5795.5")
        assert quote["ingest_run_id"] == run["run_id"] and quote["raw_id"] in archived
    assert run["status"] == "success"
    # Two roster calls (roots, then roots with their cycle) and one quote batch.
    assert http.get.call_count == 3
