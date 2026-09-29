"""Sink conformance (docs/architecture/07 §13.2).

Every sink implementation must behave identically on these scenarios:
lineage guard, provider-neutral identity (alias -> ISIN -> symbol -> mint),
mint only when allowed, park-don't-drop, and multi-provider rows on one listing.
Each scenario runs against ``InMemorySink`` and ``ClickHouseSinks`` (over a
stateful fake client).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from factorlab.ingest.datasets import (
    BarRecord,
    ConstituentRecord,
    ContractBarRecord,
    ContractRecord,
    InstrumentRecord,
    InstrumentRef,
)
from factorlab.ingest.memory import InMemorySink
from factorlab.ingest.provider import RawCapture, ingestion_run
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.v2_india import V2IndiaStorage
from factorlab.testkit.fake_clickhouse import FakeClickHouse

NOW = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)  # 09:30 IST
ISIN = "INE002A01018"


class MemoryHarness:
    name = "memory"

    def __init__(self) -> None:
        self.sink = InMemorySink()

    def listing_count(self) -> int:
        return len(self.sink.listings)

    def bars(self, table: str = "market.bars") -> list[tuple[uuid.UUID, str, str]]:
        return [(row.target_id, row.provenance.source, row.provenance.source_channel)
                for row in self.sink.rows.get(table, [])]

    def parked(self) -> int:
        return len(self.sink.unresolved)


class ClickHouseHarness:
    name = "clickhouse"

    def __init__(self) -> None:
        self.client = FakeClickHouse()
        self.sink = ClickHouseSinks(V2IndiaStorage(self.client))

    def listing_count(self) -> int:
        return len(self.client.rows("ref.listings"))

    def bars(self, table: str = "market.bars") -> list[tuple[uuid.UUID, str, str]]:
        key = "listing_id" if table == "market.bars" else "contract_id"
        return [(row[key], row["source"], row.get("source_channel", ""))
                for row in self.client.rows(table)]

    def parked(self) -> int:
        return len(self.client.log["meta.unresolved_entities"])


@pytest.fixture(params=[MemoryHarness, ClickHouseHarness], ids=lambda h: h.name)
def harness(request):
    return request.param()


def ref(kind: str, value: str, symbol: str = "RELIANCE", isin: str | None = ISIN,
        exchange: str = "NSE") -> InstrumentRef:
    return InstrumentRef(kind, value, exchange, symbol, "IN", isin=isin)


def instrument(r: InstrumentRef) -> InstrumentRecord:
    return InstrumentRecord(ref=r, name=r.trading_symbol, product_type="common",
                            currency="INR", tick_size=Decimal("0.05"))


def bar(r: InstrumentRef, minute: int = 0, price: str = "100") -> BarRecord:
    value = Decimal(price)
    return BarRecord(r, "1min", NOW + timedelta(minutes=minute), value, value, value, value,
                     volume=10)


def run(sink, source: str = "upstox"):
    return ingestion_run(sink, pipeline="conformance", source=source, market_code="IND")


def write(sink, method: str, rows, *, source: str = "upstox", channel: str | None = None,
          **kwargs):
    with run(sink, source) as ctx:
        raw_id = ctx.archive(RawCapture(b"{}", "k", "http", NOW), source_channel=channel or source)
        provenance = ctx.provenance(source_channel=channel or f"{source}:test", raw_id=raw_id,
                                    as_of_time=NOW)
        result = getattr(sink, method)(rows, provenance=provenance, **kwargs)
        ctx.succeed_unit("u", result.rows_written)
    return result


UPSTOX = ref("upstox_instrument_key", "NSE_EQ|INE002A01018")
OTHER = ref("other_symbol", "RELIANCE.NS")


def test_writes_require_the_active_run(harness):
    sink = harness.sink
    with run(sink) as ctx:
        foreign = ctx.provenance(source_channel="upstox:x", raw_id=None, as_of_time=NOW)
    with pytest.raises(RuntimeError):
        sink.upsert_instruments([instrument(UPSTOX)], provenance=foreign)
    with run(sink), pytest.raises(RuntimeError):
        sink.write_bars([bar(UPSTOX)], provenance=foreign)


def test_mint_then_resolve_other_provider_onto_same_listing(harness):
    assert write(harness.sink, "upsert_instruments", [instrument(UPSTOX)]).rows_written == 1
    result = write(harness.sink, "upsert_instruments", [instrument(OTHER)], source="other",
                   mode="alias_only")
    assert (result.rows_written, result.unresolved) == (1, 0)
    assert harness.listing_count() == 1


def _only_listing(harness) -> uuid.UUID:
    if isinstance(harness, MemoryHarness):
        return next(iter(harness.sink.listings))
    return harness.client.rows("ref.listings")[0]["listing_id"]


def test_aliases_for_returns_each_providers_own_identifier(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    write(harness.sink, "upsert_instruments", [instrument(OTHER)], source="other",
          mode="alias_only")
    listing = _only_listing(harness)
    assert harness.sink.aliases_for([listing], alias_kind="upstox_instrument_key")[listing] \
        .alias_value == "NSE_EQ|INE002A01018"
    other = harness.sink.aliases_for([listing], alias_kind="other_symbol")[listing]
    assert (other.alias_value, other.isin, other.exchange_code) == ("RELIANCE.NS", ISIN, "NSE")
    assert harness.sink.aliases_for([uuid.uuid4()], alias_kind="other_symbol") == {}


def test_unknown_instrument_is_parked_when_minting_is_not_allowed(harness):
    result = write(harness.sink, "upsert_instruments",
                   [instrument(ref("other_symbol", "NEWCO.NS", "NEWCO", isin=None))],
                   source="other", mode="alias_only")
    assert (result.rows_written, result.unresolved) == (0, 1)
    assert harness.listing_count() == 0 and harness.parked() == 1


def test_symbol_fallback_refuses_a_different_isin(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    reused = ref("other_symbol", "RELIANCE.X", isin="INE999A01011")
    result = write(harness.sink, "upsert_instruments", [instrument(reused)], source="other",
                   mode="alias_only")
    assert result.unresolved == 1 and harness.listing_count() == 1


def test_symbol_only_ref_resolves_when_unambiguous(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    hint = ref("other_symbol", "RELIANCE", isin=None)
    result = write(harness.sink, "write_bars", [bar(hint)], source="other")
    assert (result.rows_written, result.unresolved) == (1, 0)


def test_two_providers_bars_share_a_listing_and_keep_their_source(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    write(harness.sink, "upsert_instruments", [instrument(OTHER)], source="other",
          mode="alias_only")
    write(harness.sink, "write_bars", [bar(UPSTOX, 0), bar(UPSTOX, 1)],
          channel="upstox:v3_intraday")
    write(harness.sink, "write_bars", [bar(OTHER, 0)], source="other", channel="other:rest")
    rows = harness.bars()
    assert len({target for target, _, _ in rows}) == 1
    assert sorted(source for _, source, _ in rows) == ["other", "upstox", "upstox"]
    listing = rows[0][0]
    marks = harness.sink.watermarks([listing], dataset="market.bars", source="upstox",
                                    resolution="1min")
    assert marks[listing] == NOW + timedelta(minutes=1)
    assert harness.sink.watermarks([listing], dataset="market.bars", source="other",
                                   resolution="1min")[listing] == NOW


def test_unresolved_bars_are_parked_not_written(harness):
    result = write(harness.sink, "write_bars", [bar(UPSTOX)])
    assert (result.rows_written, result.unresolved) == (0, 1)
    assert harness.bars() == [] and harness.parked() == 1


def test_contracts_resolve_underlying_and_contract_bars_resolve_by_alias(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    future = ref("upstox_instrument_key", "NSE_FO|66691", "RELIANCE FUT 29 SEP 26", isin=None)
    contract = ContractRecord(ref=future, underlying=UPSTOX, product_type="single_stock_future",
                              expiry=date(2026, 9, 29), lot_size=500, tick_size=Decimal("0.1"))
    orphan = ContractRecord(
        ref=ref("upstox_instrument_key", "NSE_FO|1", "GHOST FUT", isin=None),
        underlying=ref("upstox_instrument_key", "NSE_EQ|INE000000000", "GHOST",
                       isin="INE000000000"),
        product_type="single_stock_future", expiry=date(2026, 9, 29))
    result = write(harness.sink, "upsert_contracts", [contract, orphan])
    assert (result.rows_written, result.unresolved) == (1, 1)
    value = Decimal(101)
    bars = write(harness.sink, "write_contract_bars", [ContractBarRecord(
        future, "1min", NOW, value, value, value, value, volume=5, oi=7)])
    assert bars.rows_written == 1
    assert len(harness.bars("market.futures_contract_bars")) == 1


def test_clickhouse_bar_rows_carry_provenance_and_market_calendar():
    harness = ClickHouseHarness()
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    write(harness.sink, "write_bars", [bar(UPSTOX, -30), bar(UPSTOX, 0)],
          channel="upstox:v3_intraday")
    first, second = harness.client.rows("market.bars")
    assert (first["session"], second["session"]) == ("pre", "regular")  # 09:00 / 09:30 IST
    assert second["trade_date"] == date(2026, 9, 24)
    assert second["source_channel"] == "upstox:v3_intraday"
    assert second["raw_id"] is not None and second["as_of_time"] == NOW
    assert second["product_type"] == "common" and second["country_code"] == "IN"
    alias = harness.client.rows("ref.identifier_aliases")[0]
    assert (alias["source"], alias["confidence"]) == ("upstox", "exact")


def test_clickhouse_resolution_confidence_is_recorded_on_new_aliases():
    harness = ClickHouseHarness()
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    write(harness.sink, "upsert_instruments", [instrument(OTHER)], source="other",
          mode="alias_only")
    by_kind = {a["alias_kind"]: a for a in harness.client.rows("ref.identifier_aliases")}
    assert by_kind["other_symbol"]["confidence"] == "high"  # matched on ISIN + exchange
    assert by_kind["other_symbol"]["source"] == "other"


def test_clickhouse_rewrites_are_idempotent_with_rising_versions():
    harness = ClickHouseHarness()
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    write(harness.sink, "write_bars", [bar(UPSTOX)])
    write(harness.sink, "write_bars", [bar(UPSTOX)])
    versions = [row["version"] for row in harness.client.log["market.bars"]]
    assert len(versions) == 2 and versions[1] > versions[0]
    keys = {(r["listing_id"], r["bar_time"], r["source"]) for r in harness.client.log["market.bars"]}
    assert len(keys) == 1


def test_clickhouse_raw_archive_round_trips_for_replay():
    harness = ClickHouseHarness()
    with run(harness.sink) as ctx:
        raw_id = ctx.archive(RawCapture(b"\x00payload\xff", "req", "http", NOW,
                                        metadata={"k": "v"}), source_channel="upstox:v3")
    archived = harness.sink.load_raw(raw_id)
    assert (archived.source, archived.source_channel) == ("upstox", "upstox:v3")
    assert archived.capture.body == b"\x00payload\xff"
    assert archived.capture.metadata == {"k": "v"}
    with pytest.raises(KeyError):
        harness.sink.load_raw(uuid.uuid4())


def test_existing_legacy_listing_is_found_by_alias_and_never_rekeyed():
    """A listing written by today's Upstox path keeps its id when the new path writes to it."""
    harness = ClickHouseHarness()
    storage = harness.sink.storage
    handle = storage.start_ingestion_run(pipeline="legacy", source="upstox")
    legacy = storage.sync_instruments([{
        "instrument_key": "NSE_EQ|INE002A01018", "segment": "NSE_EQ", "instrument_type": "EQ",
        "isin": ISIN, "trading_symbol": "RELIANCE", "name": "Reliance", "tick_size": 0.05,
    }])["RELIANCE"]
    storage.finish_ingestion_run(handle, status="success")
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    assert [row["listing_id"] for row in harness.client.rows("ref.listings")] == [legacy]


def test_resolve_only_writes_nothing_but_reports_resolution(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    newco = ref("other_symbol", "NEWCO.NS", "NEWCO", isin=None)
    result = write(harness.sink, "upsert_instruments", [instrument(OTHER), instrument(newco)],
                   source="other:shadow", mode="resolve_only")
    assert (result.rows_written, result.resolved, result.unresolved) == (0, 1, 1)
    listing = _only_listing(harness)
    assert harness.sink.aliases_for([listing], alias_kind="other_symbol") == {}


def test_alias_only_never_overwrites_attributes():
    harness = ClickHouseHarness()
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    renamed = InstrumentRecord(ref=OTHER, name="Something Else", product_type="etf",
                               currency="INR")
    write(harness.sink, "upsert_instruments", [renamed], source="other", mode="alias_only")
    [listing] = harness.client.rows("ref.listings")
    [security] = harness.client.rows("ref.securities")
    assert listing["trading_symbol"] == "RELIANCE" and security["security_type"] == "common"
    assert len(harness.client.log["ref.listings"]) == 1  # never rewritten


def test_unknown_reference_mode_is_rejected(harness):
    if isinstance(harness, MemoryHarness):
        pytest.skip("the in-memory sink trusts the engine's mode")
    with pytest.raises(ValueError):
        write(harness.sink, "upsert_instruments", [instrument(UPSTOX)], mode="yolo")


def test_clickhouse_period_bars_are_keyed_at_exchange_local_midnight():
    client = FakeClickHouse()
    sink = ClickHouseSinks(V2IndiaStorage(client))
    aapl = InstrumentRef("eodhd_symbol", "AAPL.US", "XNAS", "AAPL", "US", isin="US0378331005")
    write(sink, "upsert_instruments", [InstrumentRecord(ref=aapl, name="Apple",
                                                        product_type="common", currency="USD")],
          source="eodhd")
    value = Decimal(230)
    # Schwab-style stamp (05:00Z) and EODHD-style date-at-midnight-NY both key to 04:00Z.
    for stamp in (datetime(2026, 9, 23, 5, 0, tzinfo=UTC), datetime(2026, 9, 23, 4, 0, tzinfo=UTC)):
        write(sink, "write_bars", [BarRecord(aapl, "daily", stamp, value, value, value, value,
                                             volume=1)], source="eodhd")
    rows = client.log["market.bars"]
    assert {r["bar_time"] for r in rows} == {datetime(2026, 9, 23, 4, 0, tzinfo=UTC)}
    assert {r["trade_date"] for r in rows} == {date(2026, 9, 23)}
    assert {r["session"] for r in rows} == {"regular"}


def _member(r: InstrumentRef, code: str = "nifty5") -> ConstituentRecord:
    return ConstituentRecord(code, r, universe_name="Test Five")


def _seed_universe_listings(harness) -> list[InstrumentRef]:
    refs = [ref("upstox_instrument_key", f"NSE_EQ|INE00000{i}01{i}", f"SYM{i}", isin=None)
            for i in range(6)]
    write(harness.sink, "upsert_instruments", [instrument(r) for r in refs])
    return refs


def _write_members(harness, members, *, day, mode="authoritative"):
    with run(harness.sink, "github_csv") as ctx:
        provenance = ctx.provenance(source_channel="github_csv:csv", raw_id=None,
                                    as_of_time=datetime.combine(day, datetime.min.time(),
                                                                tzinfo=UTC))
        result = harness.sink.write_constituents(members, provenance=provenance, mode=mode)
        ctx.succeed_unit("u", result.rows_written)
    return result


def test_universe_snapshots_become_effective_dated_membership(harness):
    refs = _seed_universe_listings(harness)
    bare = [InstrumentRef("", "", "", r.trading_symbol, "IN") for r in refs]  # tickers only
    day1, day2 = date(2026, 9, 1), date(2026, 9, 24)
    first = _write_members(harness, [_member(r) for r in bare[:5]], day=day1)
    assert (first.rows_written, first.resolved, first.unresolved) == (5, 5, 0)
    ids1 = harness.sink.universe_members(["nifty5"], as_of=day1)
    assert len(ids1) == 5
    second = _write_members(harness, [_member(r) for r in [*bare[1:5], bare[5]]], day=day2)
    assert second.rows_written == 2  # one opened, one closed
    now_members = set(harness.sink.universe_members(["nifty5"], as_of=day2))
    before = set(harness.sink.universe_members(["nifty5"], as_of=date(2026, 9, 23)))
    assert len(now_members) == 5 and now_members != before and before == set(ids1)
    assert harness.sink.universe_members(["nifty5"], as_of=date(2026, 8, 31)) == []


def test_universe_snapshot_guard_and_non_primary_modes(harness):
    refs = _seed_universe_listings(harness)
    _write_members(harness, [_member(r) for r in refs[:5]], day=date(2026, 9, 1))
    with pytest.raises(ValueError):
        _write_members(harness, [_member(refs[0])], day=date(2026, 9, 2))  # drops 4 of 5
    shadow = _write_members(harness, [_member(r) for r in refs[1:]], day=date(2026, 9, 3),
                            mode="resolve_only")
    assert (shadow.rows_written, shadow.resolved) == (0, 5)
    assert len(harness.sink.universe_members(["nifty5"], as_of=date(2026, 9, 3))) == 5
    ghost = _member(InstrumentRef("", "", "", "GHOST", "IN"))
    parked = _write_members(harness, [*(_member(r) for r in refs[:5]), ghost],
                            day=date(2026, 9, 4))
    assert (parked.rows_written, parked.unresolved) == (0, 1)


def test_active_listings_by_exchange(harness):
    write(harness.sink, "upsert_instruments", [instrument(UPSTOX)])
    assert harness.sink.active_listings("NSE") == [_only_listing(harness)]
    assert harness.sink.active_listings("BSE") == []
