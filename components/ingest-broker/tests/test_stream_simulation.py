import json
from decimal import Decimal
from pathlib import Path

import pytest

from factorlab.components.ingest_broker.profiles import load_registry
from factorlab.components.ingest_broker.stream_simulation import cli, run_stream
from factorlab.sources.ibkr.normalize import normalize_capture
from factorlab.sources.ibkr.provider import DryRunBrokerStorage
from factorlab.sources.ibkr.stream_simulation import StreamScenario

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "simulations/ibkr/account.yaml"


def scenario():
    return StreamScenario.model_validate(
        {
            "initial_cash": "1000",
            "instruments": {"AAPL": {"con_id": 265598, "initial_price": "10"}},
            "events": [
                {
                    "kind": "order",
                    "order_id": "buy",
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": 10,
                    "limit_price": 12,
                },
                {"kind": "fill", "order_id": "buy", "quantity": 4, "price": 10, "commission": 1},
                {"kind": "cancel", "order_id": "buy"},
            ],
        }
    )


def execution_rows(storage):
    return [
        row
        for _, _, capture in storage.archived
        if json.loads(capture.body)["kind"] == "executions"
        for row in normalize_capture(capture.body)
    ]


def test_pause_resume_and_completed_session_do_not_replay_events(tmp_path):
    storage = DryRunBrokerStorage()
    state = tmp_path / "demo.json"
    registry = load_registry(CONFIG)
    config = scenario()
    assert (
        run_stream(
            registry, config, storage, session="demo", interval=0, state_path=state, max_events=2
        )
        == 0
    )
    assert json.loads(state.read_text())["next_event"] == 2
    fill = execution_rows(storage)[0]
    assert run_stream(registry, config, storage, session="demo", interval=0, state_path=state) == 0
    assert execution_rows(storage)[-1].exec_id == fill.exec_id
    assert execution_rows(storage)[-1].exec_time == fill.exec_time
    assert storage.counts["simulation:SIM_IBKR_STREAM:paper_gateway:open_orders"] == 2
    captures = len(storage.archived)
    assert run_stream(registry, config, storage, session="demo", interval=0, state_path=state) == 0
    assert len(storage.archived) == captures


def test_partial_failure_retries_identical_fill_then_advances(tmp_path):
    class FailingStorage(DryRunBrokerStorage):
        fail = True

        def write_executions(self, rows, *, provenance):
            if rows and self.fail:
                self.fail = False
                raise RuntimeError("simulated partial write failure")
            return super().write_executions(rows, provenance=provenance)

    storage = FailingStorage()
    state = tmp_path / "demo.json"
    registry = load_registry(CONFIG)
    assert (
        run_stream(registry, scenario(), storage, session="demo", interval=0, state_path=state) == 1
    )
    assert json.loads(state.read_text())["next_event"] == 1
    failed_fill = execution_rows(storage)[0]
    assert (
        run_stream(registry, scenario(), storage, session="demo", interval=0, state_path=state) == 0
    )
    retried_fill = execution_rows(storage)[1]
    assert failed_fill.exec_id == retried_fill.exec_id
    assert failed_fill.exec_time == retried_fill.exec_time
    positions = [
        normalize_capture(capture.body)
        for _, _, capture in storage.archived
        if json.loads(capture.body)["kind"] == "portfolio"
    ]
    assert positions[-1][0].position == 4  # Retry did not buy twice.


def test_changed_session_scenario_is_rejected_without_new_captures(tmp_path):
    storage = DryRunBrokerStorage()
    state = tmp_path / "demo.json"
    registry = load_registry(CONFIG)
    run_stream(
        registry, scenario(), storage, session="demo", interval=0, state_path=state, max_events=1
    )
    count = len(storage.archived)
    changed = scenario().model_copy(update={"initial_cash": Decimal(2000)})
    with pytest.raises(ValueError, match="changed"):
        run_stream(registry, changed, storage, session="demo", interval=0, state_path=state)
    assert len(storage.archived) == count


def test_interval_and_dry_run_do_not_create_checkpoints():
    storage = DryRunBrokerStorage()
    sleeps = []
    assert (
        run_stream(
            load_registry(CONFIG),
            scenario(),
            storage,
            session="demo",
            interval=30,
            sleep=sleeps.append,
        )
        == 0
    )
    assert sleeps == [30, 30]


def test_cli_preview_never_opens_a_database(monkeypatch):
    from factorlab.storage.v2_broker import V2BrokerStorage

    def forbidden():
        raise AssertionError("dry run must not open ClickHouse")

    monkeypatch.setattr(V2BrokerStorage, "from_environment", forbidden)
    assert (
        cli(
            [
                "--config",
                str(CONFIG),
                "--scenario",
                str(ROOT / "simulations/ibkr/trades.yaml"),
                "--dry-run",
                "--interval",
                "0",
            ]
        )
        == 0
    )
