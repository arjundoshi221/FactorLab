"""IBKRBrokerProvider: one run, per-mode and per-dataset failure isolation."""

from __future__ import annotations

import json
import uuid

import pytest

from factorlab.shared.ingest.provider import run_provider
from factorlab.sources.ibkr.errors import IBKRConnectError
from factorlab.sources.ibkr.provider import (
    DryRunBrokerStorage,
    IBKRBrokerProvider,
    SnapshotConfig,
)

from .conftest import make_account_value, make_fill, make_portfolio_item, make_trade


def _connector(ibs, failing=()):
    def connect(mode, config):
        if mode in failing:
            raise IBKRConnectError(f"{mode} Gateway unreachable")
        return ibs[mode]
    return connect


def _loaded(ib):
    ib.portfolio.return_value = [make_portfolio_item()]
    ib.accountValues.return_value = [make_account_value()]
    ib.reqExecutions.return_value = [make_fill()]
    ib.openTrades.return_value = [make_trade()]
    return ib


def test_both_modes_archive_then_write_every_dataset(mock_ib_paper, mock_ib_live, now_utc):
    storage = DryRunBrokerStorage()
    provider = IBKRBrokerProvider(
        storage, SnapshotConfig(),
        connector=_connector({"paper": _loaded(mock_ib_paper), "live": _loaded(mock_ib_live)}),
        clock=lambda: now_utc,
    )
    summary = run_provider(provider, storage)

    assert summary.status == "success"
    assert [u.name for u in summary.units] == [
        f"{mode}:{name}" for mode in ("paper", "live")
        for name in ("positions", "account_state", "executions", "open_orders")
    ]
    assert storage.counts == {f"{ch}:{d}": 1 for ch in ("paper_gateway", "live_gateway")
                              for d in ("positions", "account_state", "executions", "open_orders")}
    assert [(src, ch, cap.request_key) for src, ch, cap in storage.archived][:2] == [
        ("ibkr", "paper_gateway", "portfolio:paper"),
        ("ibkr", "paper_gateway", "account_values:paper"),
    ]
    mock_ib_paper.disconnect.assert_called_once()
    mock_ib_live.disconnect.assert_called_once()
    # executions look back one day from the capture clock
    since = json.loads(storage.archived[2][2].body)["request"]["since"]
    assert since == "20260918-20:30:00"


def test_unreachable_gateway_is_one_failed_unit(mock_ib_paper, now_utc):
    storage = DryRunBrokerStorage()
    provider = IBKRBrokerProvider(
        storage, connector=_connector({"paper": _loaded(mock_ib_paper)}, failing={"live"}),
        clock=lambda: now_utc,
    )
    summary = run_provider(provider, storage)
    assert summary.status == "partial"
    assert [u.name for u in summary.failed_units] == ["live:connect"]
    assert "unreachable" in summary.failed_units[0].error
    assert storage.finished[0]["status"] == "partial"


def test_dataset_failure_does_not_stop_other_datasets(mock_ib_paper, now_utc):
    ib = _loaded(mock_ib_paper)
    ib.openTrades.side_effect = TimeoutError("openTrades timed out")

    class FailingPositions(DryRunBrokerStorage):
        def write_positions(self, rows, *, provenance):
            raise RuntimeError("insert rejected")

    storage = FailingPositions()
    provider = IBKRBrokerProvider(storage, SnapshotConfig(modes=("paper",)),
                                  connector=_connector({"paper": ib}), clock=lambda: now_utc)
    summary = run_provider(provider, storage)
    assert summary.status == "partial"
    assert {u.name for u in summary.failed_units} == {"paper:positions", "paper:open_orders"}
    assert summary.rows_written == 2  # account_state + executions landed
    ib.disconnect.assert_called_once()


def test_all_gateways_down_fails_the_run(now_utc):
    storage = DryRunBrokerStorage()
    provider = IBKRBrokerProvider(storage, connector=_connector({}, failing={"paper", "live"}),
                                  clock=lambda: now_utc)
    assert run_provider(provider, storage).status == "failed"


def test_raw_id_from_archive_reaches_provenance(mock_ib_paper, now_utc):
    seen = []

    class Recording(DryRunBrokerStorage):
        def archive_raw(self, capture, *, source, source_channel):
            raw_id = uuid.uuid4()
            seen.append(raw_id)
            return raw_id

        def write_positions(self, rows, *, provenance):
            assert provenance.raw_id == seen[0]
            assert provenance.as_of_time == now_utc
            return len(rows)

    storage = Recording()
    provider = IBKRBrokerProvider(storage, SnapshotConfig(modes=("paper",)),
                                  connector=_connector({"paper": _loaded(mock_ib_paper)}),
                                  clock=lambda: now_utc)
    assert run_provider(provider, storage).status == "success"


def test_config_rejects_empty_or_duplicate_modes():
    with pytest.raises(ValueError):
        SnapshotConfig(modes=())
    with pytest.raises(ValueError):
        SnapshotConfig(modes=("paper", "paper"))
