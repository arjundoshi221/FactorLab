"""Tests for factorlab.sources.ibkr.client — env parsing, read-only rule."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from factorlab.sources.ibkr import client
from factorlab.sources.ibkr.errors import IBKRError, IBKRReadOnlyViolation


def _patch_env(monkeypatch, values: dict[str, str]) -> None:
    monkeypatch.setattr(client, "get_secret", lambda name, default=None: values.get(name, default))
    monkeypatch.setattr(client, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr(client, "find_dotenv", lambda *a, **kw: "")


def test_readonly_false_is_rejected(monkeypatch):
    _patch_env(monkeypatch, {"IBKR_PORT_PAPER": "4002"})
    with pytest.raises(IBKRReadOnlyViolation):
        client.connect(mode="paper", readonly=False)


def test_invalid_mode_rejected(monkeypatch):
    _patch_env(monkeypatch, {"IBKR_PORT_PAPER": "4002"})
    with pytest.raises(IBKRError):
        client.connect(mode="staging")  # type: ignore[arg-type]


def test_default_mode_from_env(monkeypatch):
    _patch_env(monkeypatch, {"IBKR_DEFAULT_MODE": "live", "IBKR_PORT_LIVE": "4001"})
    assert client._resolve_mode(None) == "live"


def test_mode_specific_port_used(monkeypatch):
    captured = {}

    fake_ib = MagicMock()
    fake_ib.client.serverVersion.return_value = 176
    ib_class = MagicMock(return_value=fake_ib)

    def capturing_connect(host, port, clientId, readonly, timeout):
        captured["host"] = host
        captured["port"] = port
        captured["clientId"] = clientId
        captured["readonly"] = readonly

    fake_ib.connect.side_effect = capturing_connect

    with patch("ib_async.IB", ib_class):
        _patch_env(monkeypatch, {
            "IBKR_HOST": "127.0.0.1",
            "IBKR_PORT_PAPER": "4002",
            "IBKR_PORT_LIVE": "4001",
            "IBKR_CLIENT_ID": "7",
        })

        ib_paper = client.connect(mode="paper")
        assert captured["port"] == 4002
        assert captured["readonly"] is True
        assert captured["clientId"] == 7
        assert ib_paper._factorlab_mode == "paper"

        captured.clear()
        ib_live = client.connect(mode="live", client_id=99)
        assert captured["port"] == 4001
        assert captured["clientId"] == 99
        assert ib_live._factorlab_mode == "live"


def test_env_parse_error_wrapped(monkeypatch):
    _patch_env(monkeypatch, {"IBKR_PORT_PAPER": "not-a-number"})
    with pytest.raises(IBKRError):
        client._read_config("paper")


def test_mode_of_requires_tagged_ib():
    bad = MagicMock(spec=[])
    with pytest.raises(IBKRError):
        client.mode_of(bad)


def test_source_channel_of():
    ib = MagicMock()
    ib._factorlab_mode = "paper"
    assert client.source_channel_of(ib) == "paper_gateway"
    ib._factorlab_mode = "live"
    assert client.source_channel_of(ib) == "live_gateway"


def test_connected_context_disconnects(monkeypatch):
    fake_ib = MagicMock()
    fake_ib.isConnected.return_value = True
    fake_ib.client.serverVersion.return_value = 176

    ib_class = MagicMock(return_value=fake_ib)
    _patch_env(monkeypatch, {"IBKR_PORT_PAPER": "4002"})

    with patch("ib_async.IB", ib_class):
        with client.connected(mode="paper") as ib:
            assert ib is fake_ib
    fake_ib.disconnect.assert_called_once()


def test_disconnect_is_idempotent():
    fake_ib = MagicMock()
    fake_ib.isConnected.return_value = False
    client.disconnect(fake_ib)  # should not raise
    fake_ib.disconnect.assert_not_called()
