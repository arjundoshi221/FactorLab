import json
import socket
from pathlib import Path

import pytest

from factorlab.runtime.monitor import MAX_PACKET, MonitorClient, decode_packet


@pytest.mark.parametrize("name", ["hb", "warn", "err"])
def test_golden_packets(name):
    packet = (Path(__file__).parent / "fixtures" / "monitor" / f"{name}.json").read_bytes()
    assert decode_packet(packet)["type"] == name


def test_udp_sender_size_sequence_and_fingerprint(monkeypatch):
    monkeypatch.delenv("FACTORLAB_MONITOR_ADDR", raising=False)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        client = MonitorClient("test")
        client._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client._socket.setblocking(False)
        client._target = receiver.getsockname()
        try:
            client.heartbeat()
            hb = decode_packet(receiver.recv(2048))
            client.error("auth", "🌏" * 1000, fingerprint="auth", huge="x" * 2000)
            raw = receiver.recv(2048)
            err = decode_packet(raw)
            assert len(raw) <= MAX_PACKET
            assert err["seq"] == hb["seq"] + 1
            assert err["inst"] == hb["inst"]
            assert err["fingerprint"] == "auth"
            assert "context" not in err
            another = MonitorClient("test")
            another._socket = client._socket
            another._target = client._target
            another.heartbeat()
            next_packet = decode_packet(receiver.recv(2048))
            assert next_packet["inst"] == hb["inst"]
            assert next_packet["seq"] == err["seq"] + 1
        finally:
            client._socket.close()


def test_sender_failure_and_disabled_are_noop(monkeypatch):
    monkeypatch.delenv("FACTORLAB_MONITOR_ADDR", raising=False)
    client = MonitorClient("test")
    before = client.seq
    client.error("other", "failure", non_json=object())
    assert client.seq == before
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        client._socket = sock
        client._target = ("127.0.0.1", 9)
        sock.close()
        client.heartbeat()


@pytest.mark.parametrize("value", [[], {"v": 2}, {"v": True}, {"v": 1, "type": "new"}])
def test_bad_protocol(value):
    with pytest.raises(ValueError):
        decode_packet(json.dumps(value).encode())
