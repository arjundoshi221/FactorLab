"""ClickHouse v2 migrations are forward-only: tools/schema_checksums.py and its lock."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from factorlab.schema.migrate import MIGRATION_DIR

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("schema_checksums_tool",
                                              REPO / "tools" / "schema_checksums.py")
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


def test_every_migration_matches_the_lock():
    assert tool.problems() == []


def test_waves_before_ten_are_frozen_as_applied():
    lock = tool.read_lock()
    assert {status for mid, (_, status) in lock.items() if mid < "wave_10"} == {"applied"}


@pytest.fixture
def sql(tmp_path):
    directory = tmp_path / "v2"
    shutil.copytree(MIGRATION_DIR, directory, ignore=shutil.ignore_patterns("__pycache__"))
    return directory


def test_an_edited_applied_migration_is_refused(sql, capsys):
    with (sql / "wave_01_schema.sql").open("a", encoding="utf-8") as handle:
        handle.write("\n-- edited\n")
    assert any("applied migrations never change" in p for p in tool.problems(sql, sql / tool.LOCK.name))
    assert tool.main(["update", "wave_01_schema"], sql) == 1
    assert "add a new wave" in capsys.readouterr().err


def test_a_deleted_migration_is_refused(sql):
    (sql / "wave_02_views.sql").unlink()
    assert any("was deleted" in p for p in tool.problems(sql, sql / tool.LOCK.name))


def test_new_and_edited_pending_migrations_need_an_explicit_step(sql):
    lock = sql / tool.LOCK.name
    (sql / "wave_11_schema_example.sql").write_text("CREATE TABLE x (a UInt8) ENGINE = Memory;\n")
    assert any("not in the lock" in p for p in tool.problems(sql, lock))
    assert tool.main(["add"], sql) == 0
    assert tool.read_lock(lock)["wave_11_schema_example"][1] == "pending"
    (sql / "wave_11_schema_example.sql").write_text("CREATE TABLE x (b UInt8) ENGINE = Memory;\n")
    assert any("update wave_11_schema_example" in p for p in tool.problems(sql, lock))
    assert tool.main(["update", "wave_11_schema_example"], sql) == 0
    assert tool.main(["applied", "wave_11_schema_example"], sql) == 0
    assert tool.problems(sql, lock) == []
