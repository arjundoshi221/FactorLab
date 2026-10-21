"""US futures from Schwab quotes (F-043): symbols, discovery, quote normalization.

Payloads are hand-built in Schwab's QuoteFuture/ReferenceFuture shape. They are unit
inputs, not conformance fixtures: those wait for a recorded production capture.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from factorlab.calendars.us import NY
from factorlab.ingest.datasets import ReferenceRequest
from factorlab.ingest.errors import NormalizationError
from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.futures import (
    FuturesRoot,
    candidate_symbols,
    cycle_contracts,
    normalize_futures_contracts,
    normalize_futures_quotes,
    normalize_futures_roots,
    parse_contract,
)
from factorlab.sources.schwab.settings import SchwabSettings
from factorlab.sources.schwab.sources import (
    SchwabFuturesContracts,
    SchwabFuturesQuotes,
    SchwabListings,
)

FETCHED = datetime(2026, 9, 14, 14, 0, tzinfo=UTC)
ES = FuturesRoot("/ES", "XCME", "index_future", "HMUZ", "E-mini S&P 500")
CL = FuturesRoot("/CL", "XNYM", "commodity_future", "FGHJKMNQUVXZ", "WTI crude oil")
DX = FuturesRoot("/DX", "IFUS", "fx_future", "HMUZ", "US Dollar Index")


def millis(day: date, hour: int = 12) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=NY).timestamp() * 1000)


def future(symbol: str, expiry: date | None = None, **quote: Any) -> dict[str, Any]:
    reference: dict[str, Any] = {"product": symbol[:3], "futureMultiplier": 50}
    if expiry is not None:
        reference["futureExpirationDate"] = millis(expiry, 9)
    return {
        "assetMainType": "FUTURE",
        "symbol": symbol,
        "quote": {"tick": 0.25, **quote},
        "reference": reference,
    }


def capture(payload: dict[str, Any], **metadata: Any) -> RawCapture:
    return RawCapture(
        body=json.dumps(payload).encode(),
        request_key="test",
        transport="http",
        fetched_at=FETCHED,
        metadata=metadata,
    )


def test_cycle_contracts_follow_each_roots_listing_months() -> None:
    assert cycle_contracts(ES, date(2026, 10, 21), 4) == ["/ESZ26", "/ESH27", "/ESM27", "/ESU27"]
    assert cycle_contracts(CL, date(2026, 12, 15), 3) == ["/CLZ26", "/CLF27", "/CLG27"]
    assert candidate_symbols(ES, date(2026, 12, 1)) == [
        "/ES",
        "/ESZ26",
        "/ESH27",
        "/ESM27",
        "/ESU27",
    ]


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("/ESZ26", ("/ES", 2026, 12)),
        ("/HGK27", ("/HG", 2027, 5)),  # G is also a month code; the year must still parse
        ("/ZNZ26", ("/ZN", 2026, 12)),
        ("/6EH27", ("/6E", 2027, 3)),
        ("/ES", None),
        ("AAPL", None),
    ],
)
def test_parse_contract(symbol: str, expected: tuple[str, int, int] | None) -> None:
    assert parse_contract(symbol) == expected


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"root": "ES"}, "look like"),
        ({"mic": "XNYS"}, "venue"),
        ({"product_type": "common"}, "futures product type"),
        ({"months": "HMUA"}, "month codes"),
        ({"depth": 0}, "depth"),
    ],
)
def test_roster_entries_are_validated(kwargs: dict[str, Any], message: str) -> None:
    entry = {**ES.to_mapping(), **kwargs}
    with pytest.raises(ValueError, match=message):
        FuturesRoot.from_mapping(entry)


def test_contract_plan_keeps_each_root_in_one_capture_and_respects_the_batch() -> None:
    roots = [
        FuturesRoot(f"/R{i:02d}", "XCME", "index_future", "HMUZ").to_mapping() for i in range(17)
    ]
    source = SchwabFuturesContracts(SchwabSettings(name="schwab"))
    units = source.plan(ReferenceRequest("USA", params={"roots": roots, "as_of": "2026-10-21"}))
    assert [len(u.params["symbols"]) for u in units] == [50, 35]  # 5 symbols per root
    assert [len(u.params["roots"]) for u in units] == [10, 7]
    assert units[0].params["symbols"][:2] == ["/R00", "/R00Z26"]


def test_discovery_starts_at_the_active_contract_and_skips_unquoted_roots() -> None:
    # Mid-September: the September contract still trades, but December is active.
    payload = {
        "/ES": future("/ES"),
        "/ESU26": future("/ESU26", date(2026, 9, 18)),
        "/ESZ26": future("/ESZ26", date(2026, 12, 18)),
        "/ESH27": future("/ESH27", date(2027, 3, 19)),
        "/ESM27": future("/ESM27", date(2027, 6, 18)),
        "/ESU25": future("/ESU25", date(2025, 9, 19)),  # long expired: ignored
        "errors": {"invalidSymbols": ["/DX", "/DXU26", "/DXZ26"]},
    }
    payload["/ES"]["reference"]["futureActiveSymbol"] = "/ESZ26"
    rows = normalize_futures_contracts(capture(payload, roots=[ES.to_mapping(), DX.to_mapping()]))
    assert [(r.ref.alias_value, r.expiry) for r in rows] == [
        ("/ESZ26", date(2026, 12, 18)),
        ("/ESH27", date(2027, 3, 19)),
    ]
    first = rows[0]
    assert first.underlying.alias_value == "/ES" and first.ref.exchange_code == "XCME"
    assert (first.multiplier, first.tick_size, first.product_type) == (
        50,
        Decimal("0.25"),
        "index_future",
    )


def test_discovery_without_an_active_symbol_takes_the_nearest_live_contracts() -> None:
    payload = {
        "/ESU26": future("/ESU26", date(2026, 9, 18)),
        "/ESZ26": future("/ESZ26", date(2026, 12, 18)),
        "/ESH27": future("/ESH27"),  # no expiry: cannot be dated, skipped
    }
    rows = normalize_futures_contracts(capture(payload, roots=[ES.to_mapping()]))
    assert [r.ref.alias_value for r in rows] == ["/ESU26", "/ESZ26"]


def test_roots_become_listings_named_from_the_roster() -> None:
    payload = {"/ES": future("/ES"), "/CL": future("/CL")}
    rows = normalize_futures_roots(
        capture(payload, roots=[ES.to_mapping(), CL.to_mapping(), DX.to_mapping()])
    )
    assert [(r.ref.alias_value, r.ref.exchange_code, r.product_type, r.name) for r in rows] == [
        ("/ES", "XCME", "index_future", "E-mini S&P 500"),
        ("/CL", "XNYM", "commodity_future", "WTI crude oil"),
    ]


def test_listing_source_plans_and_normalizes_futures_roots() -> None:
    source = SchwabListings(SchwabSettings(name="schwab"))
    [unit] = source.plan(
        ReferenceRequest("USA", params={"mode": "futures_roots", "roots": [ES.to_mapping()]})
    )
    assert unit.params["roots"] == [ES.to_mapping()]
    rows = source.normalize(
        capture({"/ES": future("/ES")}, roots=[ES.to_mapping()], endpoint="futures_roots")
    )
    assert [r.ref.alias_value for r in rows] == ["/ES"]


def test_quotes_map_fields_and_use_the_vendor_quote_time() -> None:
    quoted = datetime(2026, 9, 14, 13, 59, 30, tzinfo=UTC)
    traded = datetime(2026, 9, 14, 13, 59, 29, tzinfo=UTC)
    item = future(
        "/ESZ26",
        date(2026, 12, 18),
        lastPrice=5801.25,
        lastSize=3,
        bidPrice=5801,
        askPrice=5801.5,
        bidSize=12,
        askSize=9,
        openPrice=5790,
        highPrice=5805.75,
        lowPrice=5788.5,
        totalVolume=812345,
        openInterest=2104567,
        quoteTime=int(quoted.timestamp() * 1000),
        tradeTime=int(traded.timestamp() * 1000),
    )
    item["reference"]["futureSettlementPrice"] = 5795.5
    payload = {"/ESZ26": item, "errors": {"invalidSymbols": ["/ESH27"]}}
    [row] = normalize_futures_quotes(
        capture(payload, contracts={"/ESZ26": "XCME", "/ESH27": "XCME"})
    )
    assert row.contract.alias_value == "/ESZ26" and row.contract.exchange_code == "XCME"
    assert (row.observed_at, row.last_trade_at) == (quoted, traded)
    assert (row.last_price, row.bid, row.ask) == (
        Decimal("5801.25"),
        Decimal(5801),
        Decimal("5801.5"),
    )
    assert (row.day_open, row.day_high, row.day_low) == (
        Decimal(5790),
        Decimal("5805.75"),
        Decimal("5788.5"),
    )
    assert (row.last_size, row.bid_size, row.ask_size) == (3, 12, 9)
    assert (row.day_volume, row.oi, row.settlement_price) == (812345, 2104567, Decimal("5795.5"))


def test_quote_settlement_prefers_the_quote_field_and_time_falls_back_to_the_capture() -> None:
    item = future("/CLZ26", date(2026, 11, 19), lastPrice=71.42, settlementPrice=71.05)
    item["reference"]["futureSettlementPrice"] = 70.0
    [row] = normalize_futures_quotes(capture({"/CLZ26": item}, contracts={"/CLZ26": "XNYM"}))
    assert row.settlement_price == Decimal("71.05")
    assert row.observed_at == FETCHED


def test_quotes_reject_a_response_with_none_of_the_requested_contracts() -> None:
    with pytest.raises(NormalizationError, match="no requested futures"):
        normalize_futures_quotes(
            capture({"/ESZ26": {"assetMainType": "EQUITY"}}, contracts={"/ESZ26": "XCME"})
        )


def test_quote_source_batches_contracts_with_their_venues() -> None:
    contracts = {f"/R{i:02d}Z26": "XCME" for i in range(60)}
    units = SchwabFuturesQuotes(SchwabSettings(name="schwab")).plan(
        ReferenceRequest("USA", params={"contracts": contracts})
    )
    assert [len(u.params["contracts"]) for u in units] == [50, 10]
    assert units[1].instruments[0].exchange_code == "XCME"
