from __future__ import annotations

import contextlib
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import migrate_clickhouse_v2 as migration


class Result:
    def __init__(self, rows):
        self.result_rows = rows


def test_split_sql_respects_quotes_and_comments():
    script = """
    -- the semicolon below is data, not a separator
    SELECT 'a;b';
    /* block; comment */ SELECT "c;d";
    SELECT 3
    """
    statements = migration.split_sql(script)
    assert len(statements) == 3
    assert "'a;b'" in statements[0]
    assert '"c;d"' in statements[1]


def test_migrations_are_ordered_by_phase_wave_and_file(tmp_path: Path):
    for name in (
        "wave_02_backfill.sql",
        "wave_01_views.sql",
        "wave_01_schema.sql",
        "wave_01_schema_data_completion.sql",
        "wave_08_rbac.sql",
        "wave_01_validate.sql",
        "wave_01_validate_data_completion.sql",
    ):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    found = migration.discover_migrations(tmp_path)
    assert [item.migration_id for item in found] == [
        "wave_01_schema",
        "wave_01_schema_data_completion",
        "wave_01_views",
        "wave_02_backfill",
        "wave_08_rbac",
    ]
    assert found[1].phase == "schema"
    assert [path.name for path in migration.validation_files(tmp_path, 1)] == [
        "wave_01_validate.sql",
        "wave_01_validate_data_completion.sql",
    ]


def test_checksum_is_newline_stable_and_drift_is_fatal(tmp_path: Path):
    assert migration.checksum_sql("SELECT 1;\r\n") == migration.checksum_sql("SELECT 1;\n")
    item = migration.Migration("m1", 1, "schema", tmp_path / "m1.sql", "SELECT 1", "new")
    with pytest.raises(migration.MigrationError, match="checksum drift"):
        migration.ensure_no_checksum_drift(
            [item], {"m1": migration.JournalEntry("m1", "old", "succeeded")}
        )


def test_apply_dry_run_never_opens_connection(monkeypatch, capsys):
    monkeypatch.setattr(
        migration,
        "create_client",
        lambda: pytest.fail("dry-run must not connect to ClickHouse"),
    )
    assert migration.main(["apply", "--phase", "schema", "--through-wave", "1", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "wave_00_schema" in output
    assert "legacy tables are never modified" in output


def test_apply_requires_yes_before_mutating():
    args = SimpleNamespace(
        phase="schema",
        through_wave=0,
        dry_run=False,
        yes=False,
        lock_timeout=10,
        source_database="factorlab",
    )
    item = migration.Migration("m1", 0, "schema", Path("m1"), "SELECT 1", "sum")
    with pytest.raises(migration.MigrationError, match="--yes"):
        migration.command_apply(object(), args, [item])


def test_create_client_reads_production_username_and_password_file(monkeypatch, tmp_path):
    captured = {}
    password_file = tmp_path / "CLICKHOUSE_PASSWORD"
    password_file.write_text("production-secret\n", encoding="utf-8")
    monkeypatch.delenv("CLICKHOUSE_USER", raising=False)
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    monkeypatch.setenv("CLICKHOUSE_USERNAME", "factorlab")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD_FILE", str(password_file))
    monkeypatch.setattr(
        "clickhouse_connect.get_client",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    migration.create_client()

    assert captured["username"] == "factorlab"
    assert captured["password"] == "production-secret"


def test_create_client_scopes_partition_override_to_migration_client(monkeypatch):
    captured = {}
    monkeypatch.setenv("CLICKHOUSE_MIGRATION_MAX_PARTITIONS_PER_INSERT_BLOCK", "1000")
    monkeypatch.setattr(
        "clickhouse_connect.get_client",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    migration.create_client()

    assert captured["settings"] == {"max_partitions_per_insert_block": 1000}


def test_create_client_rejects_unsafe_partition_override(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_MIGRATION_MAX_PARTITIONS_PER_INSERT_BLOCK", "100000")
    with pytest.raises(migration.MigrationError, match="between 100 and 10000"):
        migration.create_client()


def test_failed_migration_is_resumed(monkeypatch):
    args = SimpleNamespace(
        phase="backfill",
        through_wave=1,
        dry_run=False,
        yes=True,
        lock_timeout=10,
        source_database="factorlab",
    )
    item = migration.Migration("m1", 1, "backfill", Path("m1"), "SELECT 1", "sum")
    applied = []
    monkeypatch.setattr(migration, "check_server_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "run_validations", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        migration, "migration_lock", lambda *args, **kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        migration,
        "load_journal",
        lambda client: {"m1": migration.JournalEntry("m1", "sum", "failed")},
    )
    monkeypatch.setattr(migration, "journal_exists", lambda client: True)
    monkeypatch.setattr(migration, "write_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        migration,
        "execute_migration",
        lambda client, selected, source, run_id: applied.append(selected.migration_id),
    )
    migration.command_apply(object(), args, [item])
    assert applied == ["m1"]


def test_succeeded_backfill_is_rerun_for_catch_up(monkeypatch):
    args = SimpleNamespace(
        phase="backfill",
        through_wave=1,
        dry_run=False,
        yes=True,
        lock_timeout=10,
        source_database="factorlab",
    )
    item = migration.Migration("m1", 1, "backfill", Path("m1"), "SELECT 1", "sum")
    applied = []
    monkeypatch.setattr(migration, "check_server_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "run_validations", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        migration, "migration_lock", lambda *args, **kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        migration,
        "load_journal",
        lambda client: {"m1": migration.JournalEntry("m1", "sum", "succeeded")},
    )
    monkeypatch.setattr(migration, "journal_exists", lambda client: True)
    monkeypatch.setattr(migration, "write_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        migration,
        "execute_migration",
        lambda client, selected, source, run_id: applied.append(selected.migration_id),
    )
    migration.command_apply(object(), args, [item])
    assert applied == ["m1"]


def test_lock_refuses_active_owner():
    class Client:
        def command(self, sql):
            if sql.startswith("CREATE TABLE"):
                raise RuntimeError("Code: 57 TABLE_ALREADY_EXISTS")

        def query(self, sql):
            from datetime import datetime

            return Result([("00000000-0000-0000-0000-000000000001", datetime.now(UTC))])

    with (
        pytest.raises(migration.MigrationError, match="holds the lock"),
        migration.migration_lock(Client(), timeout_seconds=3600),
    ):
        pass


def test_stale_lock_is_recovered_and_released():
    from datetime import datetime, timedelta

    class Client:
        def __init__(self):
            self.create_attempts = 0
            self.commands = []

        def command(self, sql):
            self.commands.append(sql)
            if sql.startswith("CREATE TABLE"):
                self.create_attempts += 1
                if self.create_attempts == 1:
                    raise RuntimeError("Code: 57 TABLE_ALREADY_EXISTS")

        def query(self, sql):
            return Result(
                [("00000000-0000-0000-0000-000000000001", datetime.now(UTC) - timedelta(hours=2))]
            )

    client = Client()
    with migration.migration_lock(client, timeout_seconds=60):
        pass
    assert client.create_attempts == 2
    assert any(sql.startswith("DROP TABLE default._factorlab") for sql in client.commands)
    assert client.commands[-1].startswith("DROP TABLE IF EXISTS")


def test_validation_reports_named_crosswalk_failure(tmp_path: Path):
    path = tmp_path / "wave_00_validate.sql"
    path.write_text("-- check: duplicate crosswalk targets\nSELECT 1;", encoding="utf-8")

    class Client:
        def query(self, sql):
            return Result([(1,)])

    failures = migration.run_validation_file(Client(), path, "factorlab")
    assert failures == ["wave_00_validate.sql: duplicate crosswalk targets (1 violations)"]


def test_source_database_identifier_is_restricted():
    with pytest.raises(migration.MigrationError, match="unsafe source"):
        migration.render_sql("SELECT 1", "factorlab; DROP DATABASE ref", migration.uuid.uuid4())


def test_deferred_manifest_is_machine_readable():
    import json

    manifest = json.loads((migration.MIGRATION_DIR / "deferred.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in manifest["items"]}
    assert "fundamentals.snapshots_pit_eom" in names
    assert "market.bars_adjusted" in names
    assert "legacy_table_deletion" in names
