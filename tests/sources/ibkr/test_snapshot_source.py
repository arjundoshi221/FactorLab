"""IBKR broker.snapshot dataset source: parity with IBKRBrokerProvider, isolation, teardown."""

from __future__ import annotations

from datetime import UTC, datetime

from factorlab.ingest.bindings import Binding
from factorlab.ingest.datasets import SnapshotRequest
from factorlab.ingest.engine import run_binding
from factorlab.ingest.memory import InMemorySink
from factorlab.ingest.provider import NullProviderStorage, run_provider
from factorlab.sources.ibkr.errors import IBKRConnectError
from factorlab.sources.ibkr.provider import IBKRBrokerProvider, SnapshotConfig
from factorlab.sources.ibkr.snapshot_source import IbkrBrokerSnapshot, IbkrSettings
from tests.sources.ibkr.conftest import (
    make_account_value,
    make_execution,
    make_fill,
    make_order,
    make_order_status,
    make_portfolio_item,
    make_trade,
)

NOW = datetime(2026, 9, 23, 20, 30, tzinfo=UTC)
BINDING = Binding(dataset="broker.snapshot", market="USA", provider="ibkr")


def _stock(ib):
    ib.portfolio.return_value = [make_portfolio_item(account=ib.managedAccounts()[0])]
    ib.accountValues.return_value = [make_account_value(account=ib.managedAccounts()[0])]
    ib.reqExecutions.return_value = [make_fill(execution=make_execution(
        time_=datetime(2026, 9, 23, 14, 5, tzinfo=UTC)))]
    ib.openTrades.return_value = [make_trade(order=make_order(), order_status=make_order_status())]
    return ib


class RecordingBrokerStorage(NullProviderStorage):
    def __init__(self):
        super().__init__()
        self.rows = []

    def _write(self, rows, *, provenance):
        self.rows += list(rows)
        return len(rows)

    write_positions = write_account_state = write_executions = write_open_orders = _write


def test_engine_run_matches_the_legacy_provider(mock_ib_paper, mock_ib_live):
    gateways = {"paper": _stock(mock_ib_paper), "live": _stock(mock_ib_live)}
    legacy = RecordingBrokerStorage()
    run_provider(IBKRBrokerProvider(legacy, SnapshotConfig(),
                                    connector=lambda mode, _: gateways[mode],
                                    clock=lambda: NOW), legacy)
    sink = InMemorySink()
    source = IbkrBrokerSnapshot(IbkrSettings(), connector=lambda mode, _: gateways[mode],
                                clock=lambda: NOW)
    summary = run_binding(BINDING, source, sink, SnapshotRequest("USA"))
    assert summary.status == "success" and len(summary.units) == 8
    new_rows = [row.record for rows in sink.rows.values() for row in rows]
    assert sorted(map(repr, new_rows)) == sorted(map(repr, legacy.rows))
    channels = {row.provenance.source_channel for rows in sink.rows.values() for row in rows}
    assert channels == {"ibkr:paper_gateway", "ibkr:live_gateway"}


def test_a_down_gateway_fails_its_units_once_and_the_other_mode_lands(mock_ib_paper):
    attempts = []

    def connector(mode, _settings):
        attempts.append(mode)
        if mode == "live":
            raise IBKRConnectError("live Gateway awaiting 2FA")
        return _stock(mock_ib_paper)

    source = IbkrBrokerSnapshot(IbkrSettings(), connector=connector, clock=lambda: NOW)
    summary = run_binding(BINDING, source, InMemorySink(), SnapshotRequest("USA"))
    assert summary.status == "partial"
    assert {u.name for u in summary.failed_units} == {
        "live:positions", "live:account_state", "live:executions", "live:open_orders"}
    assert attempts == ["paper", "live"]  # no reconnect per unit, no retries of a dead Gateway
    assert mock_ib_paper.disconnect.called or source._connections == {}


def test_engine_closes_connections_after_the_run(mock_ib_paper):
    closed = []
    source = IbkrBrokerSnapshot(IbkrSettings(modes=("paper",)),
                                connector=lambda mode, _: _stock(mock_ib_paper),
                                clock=lambda: NOW)
    source.close = lambda: closed.append(True)  # type: ignore[method-assign]
    run_binding(BINDING, source, InMemorySink(), SnapshotRequest("USA"))
    assert closed == [True]


def test_shadow_broker_snapshot_normalizes_but_never_writes(mock_ib_paper):
    sink = InMemorySink()
    source = IbkrBrokerSnapshot(IbkrSettings(modes=("paper",)),
                                connector=lambda mode, _: _stock(mock_ib_paper),
                                clock=lambda: NOW)
    shadow = Binding(dataset="broker.snapshot", market="USA", provider="ibkr", role="shadow")
    summary = run_binding(shadow, source, sink, SnapshotRequest("USA"))
    # broker.* tables have no `source` in their keys, so a shadow must not write them.
    assert (summary.status, summary.rows_written, summary.source) == ("success", 0, "ibkr:shadow")
    assert sink.rows == {} and len(sink.archived) == 4


def test_clickhouse_sink_routes_each_shape_to_its_broker_writer(mock_ib_paper):
    import uuid

    from factorlab.ingest.provider import Provenance
    from factorlab.storage.sinks import ClickHouseSinks

    class Storage(RecordingBrokerStorage):
        client = object()

        def __init__(self):
            super().__init__()
            self._active_run_id = uuid.uuid4()
            self.calls = []

        def write_positions(self, rows, *, provenance):
            self.calls.append(("positions", len(rows)))
            return len(rows)

        def write_account_state(self, rows, *, provenance):
            self.calls.append(("account_state", len(rows)))
            return len(rows)

    storage = Storage()
    sinks = ClickHouseSinks(storage)
    source = IbkrBrokerSnapshot(IbkrSettings(modes=("paper",)),
                                connector=lambda mode, _: _stock(mock_ib_paper),
                                clock=lambda: NOW)
    units = source.plan(SnapshotRequest("USA"))[:2]
    rows = [r for unit in units for r in source.normalize(source.fetch(unit))]
    provenance = Provenance("ibkr", "ibkr:paper_gateway", None, storage._active_run_id, NOW, NOW)
    assert sinks.write_snapshot(rows, provenance=provenance).rows_written == 2
    assert storage.calls == [("positions", 1), ("account_state", 1)]
