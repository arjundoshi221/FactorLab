"""Provider-agnostic engine: unit isolation, error policy, identity, replay (docs/architecture/07 §11)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from factorlab.ingest.bindings import Binding, ProviderSettings
from factorlab.ingest.datasets import (
    BarRecord,
    BarRequest,
    Capabilities,
    FetchUnit,
    InstrumentRecord,
    InstrumentRef,
    ReferenceRequest,
    SeriesWindow,
)
from factorlab.ingest.engine import bar_request, replay, run_binding
from factorlab.ingest.errors import (
    AuthRequired,
    NormalizationError,
    PermanentError,
    RateLimited,
    TransientError,
)
from factorlab.ingest.memory import InMemorySink
from factorlab.ingest.provider import RawCapture

NOW = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
ISIN = "INE002A01018"


def ref(kind: str, value: str, symbol: str = "RELIANCE") -> InstrumentRef:
    return InstrumentRef(kind, value, "NSE", symbol, "IN", isin=ISIN if symbol == "RELIANCE"
                         else None)


class FakeListings:
    """A reference source whose master is given as a list of (alias, symbol)."""

    dataset = "ref.listings"
    capabilities = Capabilities(markets=frozenset({"IND"}))

    def __init__(self, provider: str, rows: list[tuple[str, str]]) -> None:
        self.provider = provider
        self.rows = rows

    def plan(self, request: ReferenceRequest):
        return [FetchUnit("master", f"{self.provider}:master")]

    def fetch(self, unit):
        return RawCapture(json.dumps(self.rows).encode(), unit.name, "http", NOW)

    def normalize(self, capture):
        return [InstrumentRecord(ref=ref(f"{self.provider}_key", alias, symbol), name=symbol,
                                 product_type="common", currency="INR")
                for alias, symbol in json.loads(capture.body)]


class FakeBars:
    """Bar source: one unit per series; ``script`` maps unit name -> list of outcomes."""

    dataset = "market.bars"

    def __init__(self, provider: str, script: dict[str, list] | None = None,
                 instance: str | None = None) -> None:
        self.provider = provider
        self.instance = instance or provider
        self.capabilities = Capabilities(
            markets=frozenset({"IND"}), resolutions=frozenset({"1min"}),
            alias_kind=f"{provider}_key", max_lookback=timedelta(days=5), max_batch=1)
        self.script = script or {}
        self.fetches: list[str] = []

    def plan(self, request: BarRequest):
        return [FetchUnit(w.instrument.alias_value, f"{self.instance}:candles",
                          params={"key": w.instrument.alias_value},
                          instruments=(w.instrument,), start=w.start, end=w.end)
                for w in request.series]

    def fetch(self, unit):
        self.fetches.append(unit.name)
        outcomes = self.script.get(unit.name)
        if outcomes:
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        instrument = unit.instruments[0]
        return RawCapture(
            json.dumps({"price": "10"}).encode(), unit.name, "http", NOW,
            metadata={"alias_kind": instrument.alias_kind, "alias_value": instrument.alias_value,
                      "symbol": instrument.trading_symbol},
        )

    def normalize(self, capture):
        body = json.loads(capture.body)
        if "price" not in body:
            raise NormalizationError("no price")
        meta = capture.metadata
        price = Decimal(body["price"])
        return [BarRecord(ref(meta["alias_kind"], meta["alias_value"], meta["symbol"]), "1min",
                          capture.fetched_at - timedelta(minutes=1), price, price, price, price,
                          volume=1)]


def binding(provider: str, dataset: str = "market.bars", **kwargs) -> Binding:
    resolution = "1min" if dataset == "market.bars" else None
    return Binding(dataset=dataset, market="IND", provider=provider, resolution=resolution,
                   **kwargs)


def bars_request(*aliases: tuple[str, str]) -> BarRequest:
    return BarRequest("IND", "1min", tuple(
        SeriesWindow(ref(kind, value), NOW - timedelta(hours=1), NOW) for kind, value in aliases))


def seed(sink: InMemorySink, provider: str = "up", rows=(("K1", "RELIANCE"),)) -> None:
    run_binding(binding(provider, "ref.listings"), FakeListings(provider, list(rows)), sink,
                ReferenceRequest("IND"))


def test_run_writes_with_lineage_and_archives_every_capture():
    sink = InMemorySink()
    seed(sink)
    summary = run_binding(binding("up"), FakeBars("up"), sink, bars_request(("up_key", "K1")))
    assert (summary.status, summary.rows_written) == ("success", 1)
    stored = sink.rows["market.bars"][0]
    assert stored.provenance.source == "up"
    assert stored.provenance.source_channel == "up:candles"
    assert stored.provenance.raw_id in {sink.load_raw(r).raw_id for r in sink._raw}
    assert stored.provenance.as_of_time == NOW
    finished = sink.finished[-1]
    assert (finished["status"], finished["rows_written"]) == ("success", 1)


def test_two_providers_land_on_the_same_listing_with_their_own_source():
    sink = InMemorySink()
    seed(sink, "up")
    seed(sink, "alt")  # alt resolves onto up's listing via ISIN; no new listing is minted
    assert len(sink.listings) == 1
    run_binding(binding("up"), FakeBars("up"), sink, bars_request(("up_key", "K1")))
    run_binding(binding("alt", role="secondary"), FakeBars("alt"), sink,
                bars_request(("alt_key", "K1")))
    targets = {row.target_id for row in sink.rows["market.bars"]}
    sources = sorted(row.provenance.source for row in sink.rows["market.bars"])
    assert len(targets) == 1 and sources == ["alt", "up"]


def test_shadow_reference_binding_cannot_mint():
    sink = InMemorySink()
    summary = run_binding(binding("up", "ref.listings", role="shadow"),
                          FakeListings("up", [("K9", "NEWCO")]), sink, ReferenceRequest("IND"))
    assert summary.rows_written == 0 and not sink.listings
    assert sink.unresolved and sink.unresolved[0][1] == "K9"
    assert summary.source == "up:shadow"


def test_shadow_bars_are_written_under_their_own_source():
    sink = InMemorySink()
    seed(sink)
    run_binding(binding("up"), FakeBars("up"), sink, bars_request(("up_key", "K1")))
    summary = run_binding(binding("up", role="shadow"), FakeBars("up"), sink,
                          bars_request(("up_key", "K1")))
    assert summary.source == "up:shadow"
    assert sorted(r.provenance.source for r in sink.rows["market.bars"]) == ["up", "up:shadow"]
    assert [s for s, *_ in sink.archived][-1] == "up:shadow"


def test_secondary_reference_binding_only_attaches_aliases():
    sink = InMemorySink()
    seed(sink, "up")
    summary = run_binding(binding("alt", "ref.listings", role="secondary"),
                          FakeListings("alt", [("A1", "RELIANCE"), ("A9", "NEWCO")]), sink,
                          ReferenceRequest("IND"))
    assert summary.rows_written == 1 and len(sink.listings) == 1
    assert ("alt_key", "A1") in sink.aliases and ("alt_key", "A9") not in sink.aliases


def test_unit_failures_are_isolated_and_classified():
    sink = InMemorySink()
    seed(sink, rows=[("K1", "RELIANCE"), ("K2", "TCS"), ("K3", "INFY"), ("K4", "HDFC")])
    sleeps: list[float] = []
    source = FakeBars("up", script={
        "K1": [RateLimited("slow", retry_after=7)],          # retried once, then succeeds
        "K2": [TransientError("a"), TransientError("b")],     # two retries, then succeeds
        "K3": [PermanentError("gone")],                       # no retry
        "K4": [TransientError("a"), TransientError("b"), TransientError("c")],  # exhausted
    })
    summary = run_binding(binding("up"), source, sink, bars_request(
        ("up_key", "K1"), ("up_key", "K2"), ("up_key", "K3"), ("up_key", "K4")),
        sleep=sleeps.append)
    assert summary.status == "partial"
    assert {u.name for u in summary.failed_units} == {"K3", "K4"}
    assert sleeps == [7, 1.0, 2.0, 1.0, 2.0]
    assert source.fetches.count("K3") == 1 and source.fetches.count("K4") == 3


def test_auth_required_short_circuits_the_instance():
    sink = InMemorySink()
    seed(sink, rows=[("K1", "RELIANCE"), ("K2", "TCS")])
    source = FakeBars("up", script={"K1": [AuthRequired("token expired at 03:30 IST")]})
    summary = run_binding(binding("up"), source, sink,
                          bars_request(("up_key", "K1"), ("up_key", "K2")))
    assert summary.status == "failed"
    assert source.fetches == ["K1"]
    assert "skipped after auth failure" in summary.failed_units[1].error


def test_normalization_failure_still_archives_the_capture():
    sink = InMemorySink()
    seed(sink)

    class Broken(FakeBars):
        def fetch(self, unit):
            return RawCapture(b'{"nope": 1}', unit.name, "http", NOW)

    summary = run_binding(binding("up"), Broken("up"), sink, bars_request(("up_key", "K1")))
    assert summary.status == "failed"
    assert [c.request_key for _, _, c in sink.archived][-1] == "K1"


def test_foreign_source_channel_is_rejected():
    sink = InMemorySink()
    seed(sink)
    summary = run_binding(binding("up"), FakeBars("up", instance="other"), sink,
                          bars_request(("up_key", "K1")))
    assert summary.status == "failed" and "source_channel" in summary.failed_units[0].error


def test_mismatched_binding_and_disabled_binding_are_refused():
    with pytest.raises(ValueError):
        run_binding(binding("other"), FakeBars("up"), InMemorySink(), bars_request())
    with pytest.raises(ValueError):
        run_binding(binding("up", role="disabled"), FakeBars("up"), InMemorySink(),
                    bars_request())


def test_bar_request_maps_universe_to_provider_aliases_and_watermarks():
    sink = InMemorySink()
    seed(sink, rows=[("K1", "RELIANCE"), ("K2", "TCS")])
    listings = sorted(sink.listings, key=lambda lid: sink.listings[lid].trading_symbol)
    run_binding(binding("up"), FakeBars("up"), sink, bars_request(("up_key", "K1")))
    source = FakeBars("up")
    ghost = __import__("uuid").uuid4()
    request, unmapped = bar_request(binding("up"), source, [*listings, ghost],
                                    reference=sink, checkpoints=sink, now=NOW + timedelta(hours=1))
    starts = {w.instrument.alias_value: w.start for w in request.series}
    assert starts["K1"] == NOW - timedelta(minutes=1)          # resumes at its watermark
    assert starts["K2"] == NOW + timedelta(hours=1) - timedelta(days=1)  # default lookback
    assert unmapped == (str(ghost),)
    summary = run_binding(binding("up"), source, sink, request, unmapped=unmapped)
    assert "unmapped:up_key" in {u.name for u in summary.failed_units}


def test_replay_renormalizes_archived_bytes_without_fetching():
    sink = InMemorySink()
    seed(sink)
    run_binding(binding("up"), FakeBars("up"), sink, bars_request(("up_key", "K1")))
    raw_id = sink.rows["market.bars"][0].provenance.raw_id
    source = FakeBars("up")
    summary = replay(binding("up"), source, sink, [sink.load_raw(raw_id)])
    assert summary.status == "success" and summary.rows_written == 1
    assert source.fetches == []
    assert sink.rows["market.bars"][-1].provenance.raw_id == raw_id
    assert replay(binding("alt"), FakeBars("alt"), sink, [sink.load_raw(raw_id)]).status == \
        "failed"


def test_write_without_run_is_refused():
    sink = InMemorySink()
    source = FakeListings("up", [("K1", "RELIANCE")])
    capture = source.fetch(source.plan(ReferenceRequest("IND"))[0])
    import uuid

    from factorlab.ingest.provider import Provenance
    provenance = Provenance("up", "up:master", None, uuid.uuid4(), NOW, NOW)
    with pytest.raises(RuntimeError):
        sink.upsert_instruments(source.normalize(capture), provenance=provenance)


def test_settings_base_model_is_permissive():
    assert ProviderSettings.model_validate({"name": "x", "extra": 1}).name == "x"
