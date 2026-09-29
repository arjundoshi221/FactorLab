"""factorlab.orchestration.cli: bindings validation and a provider-agnostic dry run."""

from __future__ import annotations

import functools
import json

import yaml

from factorlab.ingest.provider import RawCapture
from factorlab.orchestration import cli as engine_cli
from tests.contracts import kit


class _Cli:
    """The engine CLI with every installed provider, as scripts/factlab_ingest.py runs it."""

    main = staticmethod(functools.partial(engine_cli.main, providers=kit.all_providers()))

    def __getattr__(self, name):
        return getattr(engine_cli, name)


cli = _Cli()


def test_repository_bindings_validate(capsys):
    assert cli.main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "market.bars.1min:upstox" in out and "shadow" in out


def test_dry_run_bars_uses_registry_source_and_memory_sink(tmp_path, monkeypatch, capsys):
    bindings = tmp_path / "bindings.yaml"
    bindings.write_text(yaml.safe_dump({"version": 1, "bindings": [
        {"dataset": "market.bars", "market": "IND", "resolution": "1min", "provider": "upstox",
         "role": "primary", "params": {"mode": "quote"}},
    ]}), encoding="utf-8")
    quote = {name: c for name, c, _ in kit.cases("upstox", "market.bars")}["quote_batch"]

    def fake_get(self, url, *, request_key, auth=True, metadata=None):
        return RawCapture(quote.body, request_key, "http", quote.fetched_at,
                          metadata=dict(metadata or {}))

    from factorlab.sources.upstox.client import UpstoxClient
    monkeypatch.setattr(UpstoxClient, "get", fake_get)
    code = cli.main(["--bindings", str(bindings), "run", "--dataset", "market.bars",
                     "--market", "IND", "--resolution", "1min", "--dry-run",
                     "--instrument", "NSE_EQ|INE002A01018,RELIANCE,INE002A01018"])
    report = json.loads(capsys.readouterr().out)
    # The empty in-memory sink knows no listing, so the bars are parked, not written.
    assert (code, report["status"], report["rows_written"]) == (0, "success", 0)
    assert report["pipeline"] == "market.bars.1min:upstox"


def test_session_gate_uses_the_exchange_calendar():
    from datetime import UTC, datetime

    assert cli.in_session("IND", datetime(2026, 9, 23, 4, 30, tzinfo=UTC))       # Wed 10:00 IST
    assert not cli.in_session("IND", datetime(2026, 9, 23, 11, 0, tzinfo=UTC))   # 16:30 IST
    assert not cli.in_session("IND", datetime(2026, 9, 20, 4, 30, tzinfo=UTC))   # Sunday
    assert cli.in_session("USA", datetime(2026, 9, 23, 14, 0, tzinfo=UTC))       # 10:00 ET
    assert cli.in_session("USA", datetime(2026, 9, 23, 13, 15, tzinfo=UTC), pre_open_min=30)


def test_daemon_runs_cycles_until_shutdown(monkeypatch):
    from factorlab import runtime

    cycles = []

    class Shutdown:
        def __init__(self, *_):
            self.triggered = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    holder = {}

    def fake_shutdown(*args):
        holder["s"] = Shutdown()
        return holder["s"]

    def fake_run(args):
        cycles.append(args.dataset)
        holder["s"].triggered = True
        return 0

    class Beat:
        def __init__(self, service):
            self.service = service

        def tick(self):
            cycles.append("tick")

    monkeypatch.setattr(runtime, "GracefulShutdown", fake_shutdown)
    monkeypatch.setattr(runtime, "Heartbeat", Beat)
    monkeypatch.setattr(engine_cli, "cmd_run", fake_run)
    assert cli.main(["daemon", "--dataset", "ref.listings", "--market", "IND",
                     "--interval-seconds", "1"]) == 0
    assert cycles == ["ref.listings", "tick"]
