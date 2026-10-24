"""IBKR Flex Web Service: request/poll transport, token redaction, statement normalization."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from factorlab.ingest.datasets import SnapshotRequest
from factorlab.ingest.errors import AuthRequired, PermanentError, RateLimited, TransientError
from factorlab.sources.ibkr.errors import IBKRCaptureError
from factorlab.sources.ibkr.flex import FlexClient, FlexSettings
from factorlab.sources.ibkr.flex_normalize import (
    ORDER_SUMMARY_EVENT_SEQ,
    flex_time,
    mode_for_account,
    normalize_flex_statement,
)
from factorlab.sources.ibkr.flex_source import IbkrFlexActivity, IbkrFlexSettings
from factorlab.sources.ibkr.shapes import CashFlowRecord, ExecutionRecord, OrderEventRecord

STATEMENT = (
    Path(__file__).parent / "fixtures" / "broker.flex_activity" / "live_statement.xml"
).read_bytes()
TOKEN = "123456789012345678901234"
NOW = datetime(2026, 10, 24, 17, 45, tzinfo=UTC)

SENT = (
    b"<FlexStatementResponse timestamp='24 October, 2026 01:45 PM EDT'>"
    b"<Status>Success</Status><ReferenceCode>1111111111</ReferenceCode>"
    b"<Url>https://gdcdyn.example/GetStatement</Url></FlexStatementResponse>"
)


def _fail(code: str, message: str = "nope") -> bytes:
    return (
        f"<FlexStatementResponse><Status>Fail</Status><ErrorCode>{code}</ErrorCode>"
        f"<ErrorMessage>{message}</ErrorMessage></FlexStatementResponse>"
    ).encode()


class Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.content = body
        self.status_code = status
        self.headers = {"Content-Type": "text/xml", "Set-Cookie": "secret"}


class Session:
    def __init__(self, *bodies: bytes) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append((url, dict(params)))
        return Response(self.bodies.pop(0))


def _client(session: Session, secrets: dict[str, str] | None = None) -> FlexClient:
    values = {"IBKR_FLEX_TOKEN": TOKEN, "IBKR_FLEX_QUERY_ID": "424242"}
    if secrets is not None:
        values = secrets
    return FlexClient(
        FlexSettings(poll_interval=0, poll_attempts=3),
        session=session,  # type: ignore[arg-type]
        secret=values.get,
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def test_fetch_polls_until_the_statement_is_ready_and_keeps_the_token_out_of_the_capture():
    session = Session(SENT, _fail("1019", "Statement generation in progress"), STATEMENT)

    capture = _client(session).fetch_statement()

    assert capture.body == STATEMENT
    assert capture.transport == "http"
    assert capture.fetched_at == NOW
    assert capture.content_type == "application/xml"
    send, *polls = session.calls
    assert send == (
        "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest",
        {"t": TOKEN, "q": "424242", "v": "3"},
    )
    assert [p for _, p in polls] == [{"t": TOKEN, "q": "1111111111", "v": "3"}] * 2
    assert polls[0][0] == "https://gdcdyn.example/GetStatement"
    exposed = f"{capture.source_url}{capture.metadata}{capture.headers}{capture.request_key}"
    assert TOKEN not in exposed
    assert "Set-Cookie" not in capture.headers


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("1012", AuthRequired),  # token expired
        ("1013", AuthRequired),  # IP restriction
        ("1015", AuthRequired),  # token invalid
        ("1018", RateLimited),
        ("1009", TransientError),  # server under heavy load
        ("1014", PermanentError),  # query invalid
    ],
)
def test_flex_error_codes_map_to_the_error_taxonomy(code, error):
    with pytest.raises(error) as raised:
        _client(Session(_fail(code))).fetch_statement()
    assert TOKEN not in str(raised.value)


def test_a_statement_that_never_finishes_is_transient():
    progress = _fail("1019", "Statement generation in progress")
    with pytest.raises(TransientError, match="not ready after 3 polls"):
        _client(Session(SENT, progress, progress, progress)).fetch_statement()


@pytest.mark.parametrize("missing", ["IBKR_FLEX_TOKEN", "IBKR_FLEX_QUERY_ID"])
def test_missing_secrets_fail_before_any_request(missing):
    secrets = {"IBKR_FLEX_TOKEN": TOKEN, "IBKR_FLEX_QUERY_ID": "424242"}
    del secrets[missing]
    session = Session()
    with pytest.raises(AuthRequired, match=missing):
        _client(session, secrets).fetch_statement()
    assert session.calls == []


def test_flex_times_are_eastern_wall_time():
    assert flex_time("20260115;100500") == datetime(2026, 1, 15, 15, 5, tzinfo=UTC)  # EST
    assert flex_time("2026-07-02;15:59:58") == datetime(2026, 7, 2, 19, 59, 58, tzinfo=UTC)
    assert flex_time("20260403") == datetime(2026, 4, 3, 4, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        flex_time("")


def test_mode_comes_from_the_account_id():
    assert mode_for_account("U0000001") == "live"
    assert mode_for_account("DU0000002") == "paper"


def test_statement_normalizes_fills_orders_and_cash():
    rows = normalize_flex_statement(STATEMENT)
    fills = [r for r in rows if isinstance(r, ExecutionRecord)]
    orders = [r for r in rows if isinstance(r, OrderEventRecord)]
    cash = [r for r in rows if isinstance(r, CashFlowRecord)]

    # The "(Ca.)" cancellation leg is not a fill.
    assert [f.exec_id for f in fills] == [
        "0001f8a3.66eb01c2.01.01",
        "0001f8a3.66eb01c2.01.02",
        "0001f8a3.66eb01c2.02.01",
    ]
    sell = fills[2]
    assert (sell.side, sell.quantity, sell.price) == ("SELL", Decimal(25), Decimal("410.5"))
    assert sell.commission == Decimal(1)  # Flex cost -1 -> Gateway sign
    assert sell.realized_pnl == Decimal("812.25")
    assert sell.placed_by_client == 0  # isAPIOrder="N": manual
    assert fills[0].placed_by_client is None  # API order, client id unknown
    assert (sell.order_id, sell.perm_id, sell.order_ref) == (0, 987654400, "rebalance-2026-07")

    assert [(o.perm_id, o.filled_qty_at_event) for o in orders] == [
        (987654321, Decimal(100)),
        (987654400, Decimal(25)),
    ]
    assert {o.event_seq for o in orders} == {ORDER_SUMMARY_EVENT_SEQ}
    assert orders[0].avg_fill_price_at_event == Decimal("175.328")

    by_type = {(c.account_mode, c.cash_flow_type): c for c in cash}
    assert len(cash) == 5  # SUMMARY row skipped
    dividend = by_type["live", "dividend"]
    assert dividend.vendor_id == "265598"
    assert (dividend.ex_date, dividend.pay_date) == (
        datetime(2026, 2, 9).date(),
        datetime(2026, 2, 12).date(),
    )
    assert by_type["live", "tax_withholding"].amount == Decimal("-3.9")
    assert by_type["live", "deposit"].amount == Decimal(25000)
    assert by_type["paper", "withdrawal"].account_id == "DU0000002"


def test_an_error_envelope_is_not_a_statement():
    with pytest.raises(IBKRCaptureError, match="FlexStatementResponse"):
        normalize_flex_statement(_fail("1012"))
    with pytest.raises(IBKRCaptureError, match="not valid XML"):
        normalize_flex_statement(b"<FlexQueryResponse")


def test_source_plans_one_statement_unit_and_fetches_through_the_client():
    session = Session(SENT, STATEMENT)
    source = IbkrFlexActivity(IbkrFlexSettings(), client=lambda _: _client(session))

    (unit,) = source.plan(SnapshotRequest("USA"))

    assert unit.source_channel == "ibkr:flex_statement"
    capture = source.fetch(unit)
    assert len(source.normalize(capture)) == 10
