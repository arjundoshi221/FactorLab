"""The futures recorder's schedule: roster per session, quotes while open, daily bars (F-043)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from factorlab.calendars.us import NY
from factorlab.components.ingest_us.futures import (
    FuturesRecorder,
    daily_bar,
    last_closed_session,
    load_roster,
    settlement_after,
)
from factorlab.ingest.memory import InMemorySink
from factorlab.sources.schwab.futures import FuturesRoot

ES = FuturesRoot("/ES", "XCME", "index_future", "HMUZ", "E-mini S&P 500")
ESZ26 = UUID(int=1)
RAW = UUID(int=9)


def ny(*args: int) -> datetime:
    return datetime(*args, tzinfo=NY).astimezone(UTC)


def close_row(**values: Any) -> dict[str, Any]:
    row = {
        "contract_id": ESZ26,
        "source_symbol": "/ESZ26",
        "day_open": Decimal(5790),
        "day_high": Decimal(5810),
        "day_low": Decimal(5780),
        "last_price": Decimal("5801.25"),
        "day_volume": 900_000,
        "oi": 2_000_000,
        "raw_id": RAW,
    }
    return {**row, **values}


class FakeStorage:
    def __init__(self) -> None:
        self.settlement: dict[UUID, Decimal] = {ESZ26: Decimal("5800.5")}
        self.settlement_queries: list[tuple[date, datetime]] = []

    def futures_session_closes(self, day, *, source="schwab"):
        return [close_row()]

    def futures_settlements(self, day, *, after, source="schwab"):
        self.settlement_queries.append((day, after))
        return dict(self.settlement)

    def source_status(self, status, detail, *, source="schwab"):
        pass


class FakeClient:
    def __init__(self) -> None:
        self.rosters: list[date] = []
        self.polls: list[dict[str, str]] = []

    def collect_futures_roster(self, ctx, sink, roots, as_of):
        assert [r["root"] for r in roots] == ["/ES"]
        self.rosters.append(as_of)
        sink.roster["/ESZ26"] = "XCME"

    def collect_futures_quotes(self, ctx, sink, contracts):
        self.polls.append(dict(contracts))


class Sink(InMemorySink):
    def __init__(self) -> None:
        super().__init__()
        self.roster: dict[str, str] = {}
        self.contract_aliases[("schwab_symbol", "/ESZ26")] = ESZ26


@pytest.fixture
def recorder() -> FuturesRecorder:
    return FuturesRecorder(FakeStorage(), FakeClient(), Sink(), [ES])


def bars(recorder: FuturesRecorder) -> list[Any]:
    return [row.record for row in recorder.sink.rows.get("market.futures_contract_bars", [])]


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (ny(2026, 10, 19, 10, 0), date(2026, 10, 16)),  # Monday morning: Friday closed last
        (ny(2026, 10, 20, 10, 0), date(2026, 10, 19)),
        (ny(2026, 10, 19, 17, 30), date(2026, 10, 19)),  # break: the session just closed
        (ny(2026, 10, 19, 18, 30), date(2026, 10, 19)),  # Tuesday's session is open
        (ny(2026, 10, 24, 12, 0), date(2026, 10, 23)),  # Saturday
    ],
)
def test_last_closed_session(at: datetime, expected: date) -> None:
    assert last_closed_session(at) == expected


def test_settlements_are_trusted_an_hour_into_the_next_session() -> None:
    assert settlement_after(date(2026, 10, 19)) == ny(2026, 10, 19, 19, 0)
    assert settlement_after(date(2026, 10, 23)) == ny(2026, 10, 25, 19, 0)  # Friday -> Sunday


def test_daily_bar_closes_at_the_settlement_when_it_fits_the_range() -> None:
    bar = daily_bar(close_row(), date(2026, 10, 19), "XCME", Decimal("5800.5"))
    assert bar is not None
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal(5790),
        Decimal(5810),
        Decimal(5780),
        Decimal("5800.5"),
    )
    assert (bar.volume, bar.oi, bar.resolution) == (900_000, 2_000_000, "daily")
    assert bar.bar_time.astimezone(NY).date() == date(2026, 10, 19)
    assert bar.contract.alias_value == "/ESZ26" and bar.contract.exchange_code == "XCME"


def test_daily_bar_falls_back_to_the_last_price() -> None:
    outside = daily_bar(close_row(), date(2026, 10, 19), "XCME", Decimal(5900))
    assert outside is not None and outside.close == Decimal("5801.25")
    assert daily_bar(close_row(last_price=None), date(2026, 10, 19), "XCME", None) is None
    # A last price outside the day's range cannot make a valid bar: skipped, not raised.
    assert daily_bar(close_row(last_price=Decimal(6000)), date(2026, 10, 19), "XCME", None) is None


def test_open_market_polls_quotes_and_finalizes_the_previous_session(recorder) -> None:
    wake = recorder.tick(ny(2026, 10, 19, 10, 0, 20))
    assert recorder.client.rosters == [date(2026, 10, 19)]
    assert recorder.client.polls == [{"/ESZ26": "XCME"}]
    [bar] = bars(recorder)
    assert bar.close == Decimal("5800.5")  # Friday's settlement
    assert recorder.storage.settlement_queries == [(date(2026, 10, 16), ny(2026, 10, 18, 19, 0))]
    assert wake == ny(2026, 10, 19, 10, 1, 5)
    recorder.tick(ny(2026, 10, 19, 10, 1, 5))
    assert len(recorder.client.polls) == 2 and len(bars(recorder)) == 1  # final once
    assert recorder.client.rosters == [date(2026, 10, 19)]  # same session, same roster


def test_close_writes_provisional_bars_then_settles_them_next_session(recorder) -> None:
    recorder.tick(ny(2026, 10, 19, 10, 0))
    recorder.storage.settlement = {}  # nothing for Monday until the next session reports it
    wake = recorder.tick(ny(2026, 10, 19, 17, 30))
    assert wake == ny(2026, 10, 19, 17, 45)
    assert len(recorder.client.polls) == 1  # no quotes during the break
    assert bars(recorder)[-1].close == Decimal("5801.25")  # provisional: last price
    recorder.tick(ny(2026, 10, 19, 18, 30))  # Tuesday's session: new roster, too early to settle
    assert recorder.client.rosters == [date(2026, 10, 19), date(2026, 10, 19)]
    assert len(bars(recorder)) == 2
    recorder.storage.settlement = {ESZ26: Decimal(5795)}
    recorder.tick(ny(2026, 10, 19, 19, 5))
    assert bars(recorder)[-1].close == Decimal(5795)
    assert len(bars(recorder)) == 3


def test_weekend_writes_fridays_provisional_bars_once(recorder) -> None:
    wake = recorder.tick(ny(2026, 10, 24, 12, 0))
    assert wake == ny(2026, 10, 24, 12, 15)
    recorder.tick(ny(2026, 10, 24, 12, 15))
    assert recorder.client.polls == []
    assert len(bars(recorder)) == 1


def test_an_empty_roster_refresh_is_an_error(recorder) -> None:
    recorder.client.collect_futures_roster = lambda ctx, sink, roots, as_of: None
    with pytest.raises(RuntimeError, match="no contracts"):
        recorder.tick(ny(2026, 10, 19, 10, 0))


def test_the_shipped_roster_is_valid() -> None:
    config = Path(__file__).parents[3] / "configs" / "universes" / "us_futures.yaml"
    roots = load_roster(config)
    assert len(roots) == 14
    # /6J: 7-decimal tick; /VX and /DX: Schwab answers invalidSymbols (roster comment).
    assert not {"/6J", "/VX", "/DX"} & {r.root for r in roots}
