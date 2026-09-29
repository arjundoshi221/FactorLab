"""factorlab-db dispatches to the schema library."""

from __future__ import annotations

from factorlab.components.schema_migrator import cli


def test_help_lists_every_command(capsys):
    assert cli.main([]) == 2
    out = capsys.readouterr().out
    assert all(name in out for name in ("bootstrap", "verify-writes", "migrate"))


def test_bootstrap_runs_readiness_then_the_storage_policy_check(monkeypatch):
    calls = []
    monkeypatch.setattr("factorlab.schema.readiness.main", lambda: calls.append("readiness"))
    monkeypatch.setattr("factorlab.schema.storage_policy.main", lambda: calls.append("policy"))
    assert cli.main(["bootstrap"]) == 0
    assert calls == ["readiness", "policy"]


def test_migrate_forwards_its_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "factorlab.schema.migrate.main", lambda argv: seen.setdefault("argv", argv) and 0
    )
    cli.main(["migrate", "plan", "--through-wave", "9"])
    assert seen["argv"] == ["plan", "--through-wave", "9"]
