import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from factorlab.components.monitor.service import Monitor, Storage

FIXTURES = Path(__file__).resolve().parents[3] / "libs/runtime/tests/fixtures/monitor"


@pytest.mark.parametrize("name", ["hb", "warn", "err"])
def test_golden_packets(name):
    monitor = Monitor({}, clock=lambda: 1790000000, alert=lambda *a, **k: None)
    monitor.receive((FIXTURES / f"{name}.json").read_bytes())
    assert monitor.invalid_packets == 0
    assert any(e["type"] == name and e["origin"] == "sender" for e in monitor.events)


def setup_monitor():
    now = [1790000000.0]
    alerts = []
    m = Monitor(
        {"svc": {"interval_s": 10}},
        clock=lambda: now[0],
        alert=lambda *a, **k: alerts.append((a, k)),
    )
    instance = str(uuid4())

    def send(seq=1, state="running", inst=instance, **extra):
        p = {
            "v": 1,
            "type": "hb",
            "id": "svc",
            "inst": inst,
            "seq": seq,
            "ts": int(now[0] * 1000),
            "host": "test",
            "interval_s": 10,
            "state": state,
        }
        p.update(extra)
        m.receive(json.dumps(p).encode())

    return m, now, alerts, send


def test_deadlines_recovery_and_clean_stop():
    m, now, alerts, send = setup_monitor()
    send()
    now[0] += 20
    m.check()
    assert m.services["svc"]["status"] == "late"
    now[0] += 10
    m.check()
    assert m.services["svc"]["status"] == "down"
    assert alerts[-1][1]["severity"] == "fail"
    send(seq=2)
    assert alerts[-1][0][0] == "svc: recovered"
    send(seq=3, state="stopping")
    count = len(alerts)
    now[0] += 100
    m.check()
    assert m.services["svc"]["status"] == "stopped"
    assert len(alerts) == count


def test_restart_loss_and_reordered_packets():
    m, _now, alerts, send = setup_monitor()
    send()
    send(seq=4)
    send(seq=2)
    assert m.services["svc"]["packet_loss"] == 2
    assert m.services["svc"]["seq"] == 4
    send(inst=str(uuid4()))
    assert alerts[-1][0][0] == "svc: unexpected restart"
    assert len(m.services["svc"]["restarts"]) == 1
    send(seq=5)  # retired instance cannot revive
    assert len(m.services["svc"]["restarts"]) == 1
    send(inst=m.services["svc"]["instance_id"], seq=2, state="stopping")
    count = len(alerts)
    send(inst=str(uuid4()))
    assert len(alerts) == count


def test_receive_clock_and_unknown_service_and_invalid_data():
    m, now, alerts, send = setup_monitor()
    send(ts=1)
    assert any(e["error_type"] == "clock_skew" for e in m.events)
    now[0] += 10
    m.check()
    assert m.services["svc"]["status"] == "up"
    for bad in (b"[]", b"bad json", b"x" * 1201):
        m.receive(bad)
    assert m.invalid_packets == 3
    send(id="extra")
    send(id="extra", seq=2)
    assert sum(a[0][0] == "extra: unregistered" for a in alerts) == 1


def test_job_deadline_and_window():
    m, now, _alerts, send = setup_monitor()
    m.registry["svc"] = {"max_silence_s": 93600}
    send()
    now[0] += 93600
    m.check()
    assert m.services["svc"]["status"] == "down"


def test_storage_outage_alert_and_replay(tmp_path):
    m, _now, alerts, send = setup_monitor()
    inserts = []
    online = [False]

    def connect():
        if not online[0]:
            raise OSError("ClickHouse offline")
        return SimpleNamespace(
            client=SimpleNamespace(insert=lambda table, data, **kw: inserts.append((table, data))),
            close=lambda: None,
        )

    storage = Storage(m, tmp_path, connect)
    send(type="err", error_type="auth", message="Rejected", severity="fatal")
    assert alerts[-1][1]["severity"] == "fatal"
    storage.flush()
    assert list((tmp_path / "spool").glob("*.jsonl"))
    assert not m.events
    online[0] = True
    storage.flush()
    assert not list((tmp_path / "spool").glob("*.jsonl"))
    assert any(t == "meta.service_events" for t, _ in inserts)
    storage.flush()
    assert sum(t == "meta.service_events" for t, _ in inserts) == 1


def test_silent_registered_service_down():
    m, now, _alerts, _send = setup_monitor()
    now[0] += 30
    m.check()
    assert m.services["svc"]["status"] == "down"


def test_stopped_job_still_has_next_run_deadline():
    m, now, _alerts, send = setup_monitor()
    m.registry["svc"] = {"max_silence_s": 93600}
    send(state="stopping")
    now[0] += 93600
    m.check()
    assert m.services["svc"]["status"] == "down"


def test_warning_threshold_and_fatal_dedupe():
    m, _now, alerts, send = setup_monitor()
    for seq in range(1, 11):
        send(type="warn", seq=seq, error_type="transient", message="Retry")
    assert len(alerts) == 1
    send(type="err", seq=11, error_type="auth", message="Rejected", severity="fatal")
    send(type="err", seq=12, error_type="auth", message="Rejected", severity="fatal")
    assert len(alerts) == 2
    assert len(m.services["svc"]["errors"]) == 2


def test_active_window_has_grace_when_opening_and_overnight_weekday():
    from datetime import UTC, datetime

    m, now, _alerts, send = setup_monitor()
    m.registry["svc"] = {
        "interval_s": 10,
        "active_window": {"timezone": "UTC", "days": [1], "start": "22:00", "end": "02:00"},
    }
    now[0] = datetime(2026, 9, 28, 21, 0, tzinfo=UTC).timestamp()
    m.check()
    assert m.services["svc"]["status"] == "unknown"
    now[0] = datetime(2026, 9, 28, 22, 0, tzinfo=UTC).timestamp()
    m.check()
    assert m.services["svc"]["status"] == "unknown"
    now[0] += 30
    m.check()
    assert m.services["svc"]["status"] == "down"
    send()
    now[0] = datetime(2026, 9, 29, 1, 0, tzinfo=UTC).timestamp()
    m.check()
    assert m.services["svc"]["status"] == "down"


def test_optional_profile_waits_for_first_packet():
    m, now, alerts, send = setup_monitor()
    m.registry["svc"]["profile"] = "ibkr"
    now[0] += 100
    m.check()
    assert not alerts
    send()
    now[0] += 30
    m.check()
    assert alerts[-1][1]["severity"] == "fail"


def test_clickhouse_pressure_alerts_once_and_clears():
    m, now, alerts, _send = setup_monitor()
    gib = 2**30
    m.check_clickhouse(5_000, 900, 1 * gib, 4 * gib)
    assert not alerts
    m.check_clickhouse(9_000, 40_000, 1 * gib, 4 * gib)
    assert alerts[-1][0][0] == "clickhouse: memory pressure"
    assert "49000 data parts" in alerts[-1][0][1]
    now[0] += 3600
    m.check_clickhouse(9_000, 40_000, 3.9 * gib, 4 * gib)
    assert len(alerts) == 1  # still the same episode
    m.check_clickhouse(5_000, 900, 1 * gib, 4 * gib)
    m.check_clickhouse(5_000, 900, 3.5 * gib, 4 * gib)
    assert len(alerts) == 2 and "memory 3.5 of 4.0 GiB" in alerts[-1][0][1]


def test_clickhouse_probe_survives_an_unavailable_server(tmp_path):
    m, _now, alerts, _send = setup_monitor()

    def offline():
        raise OSError("ClickHouse offline")

    Storage(m, tmp_path, offline).probe_clickhouse()
    rows = [(20_000, 0, 0, 0)]
    online = SimpleNamespace(
        client=SimpleNamespace(query=lambda sql: SimpleNamespace(result_rows=rows)),
        close=lambda: None,
    )
    Storage(m, tmp_path, lambda: online).probe_clickhouse()
    assert alerts[-1][0][0] == "clickhouse: memory pressure"
