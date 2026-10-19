import json
from datetime import UTC, date, datetime
from uuid import UUID

from factorlab.ingest.profiles import ProfileAccount
from factorlab.ingest.provider import run_provider
from factorlab.sources.ibkr.normalize import normalize_capture
from factorlab.sources.ibkr.provider import DryRunBrokerStorage
from factorlab.sources.ibkr.simulation import STEPS, simulated_provider


def account(name):
    return ProfileAccount(
        user_id=UUID("d677331a-a04f-454a-8a1e-02425be2c101"),
        broker_code="ibkr",
        account_id=name,
        account_mode="paper",
        base_currency="USD",
        booking_country="US",
        opened_date=date(2026, 10, 1),
        simulated=True,
    )


def test_two_accounts_same_instrument_different_positions_and_stable_fills():
    executions = []
    for name, quantity in (("SIM_A", 10), ("SIM_B", 20)):
        for _ in range(2):
            storage = DryRunBrokerStorage()
            result = run_provider(
                simulated_provider(
                    storage, account(name), "filled", at=datetime.now(UTC), quantity=quantity
                ),
                storage,
            )
            assert result.status == "success"
            captures = {
                json.loads(capture.body)["kind"]: capture for _, _, capture in storage.archived
            }
            position = normalize_capture(captures["portfolio"].body)[0]
            assert position.account_id == name and position.position == quantity
            assert position.trading_symbol == "AAPL"
            executions.append(normalize_capture(captures["executions"].body)[0])
            since = json.loads(captures["executions"].body)["request"]["since"]
            assert since <= executions[-1].exec_time.strftime("%Y%m%d-%H:%M:%S")
            assert all(
                capture.transport == "file" and capture.metadata["simulated"]
                for capture in captures.values()
            )
            assert all(
                channel.startswith(f"simulation:{name}:") for _, channel, _ in storage.archived
            )
    assert executions[0].exec_id == executions[1].exec_id
    assert executions[0].exec_time == executions[1].exec_time
    assert executions[0].exec_id != executions[2].exec_id


def test_empty_snapshots_are_recorded_after_close():
    storage = DryRunBrokerStorage()
    for step in STEPS:
        result = run_provider(
            simulated_provider(storage, account("SIM_A"), step, at=datetime.now(UTC)), storage
        )
        assert result.status == "success"
    last = storage.archived[-4:]
    assert normalize_capture(last[0][2].body) == []
    assert normalize_capture(last[3][2].body) == []
    assert {item["dataset"] for item in storage.snapshots[-4:]} == {
        "positions",
        "account_state",
        "executions",
        "open_orders",
    }


def test_failed_capture_does_not_publish_a_successful_snapshot():
    class FailPositions(DryRunBrokerStorage):
        def write_positions(self, rows, *, provenance):
            raise RuntimeError("write failed")

    storage = FailPositions()
    result = run_provider(
        simulated_provider(storage, account("SIM_A"), "filled", at=datetime.now(UTC)), storage
    )
    assert result.status == "partial"
    assert "positions" not in {item["dataset"] for item in storage.snapshots}
