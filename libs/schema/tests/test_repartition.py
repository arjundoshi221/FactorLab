"""Wave 16 repartition: copy only with writers stopped, verify, then swap exactly once."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from factorlab.schema import migrate, repartition
from factorlab.schema.migrate import MigrationError
from factorlab.schema.repartition import SWAPS, Swap

MONTHLY = "(product_type, resolution, toYYYYMM(bar_time))"
YEARLY = (
    "(product_type, resolution, if(resolution IN ('daily'), toYear(bar_time), toYYYYMM(bar_time)))"
)
BARS = SWAPS[0]


class Server:
    """Answers the catalog queries repartition issues and records its mutations."""

    def __init__(self):
        self.keys = {"market.bars": MONTHLY, "market.bars_v16": YEARLY}
        self.cols = {
            name: [("listing_id", "UUID"), ("bar_time", "DateTime64(3, 'UTC')")]
            for name in self.keys
        }
        self.partitions = [("a", 10), ("b", 5)]
        self.writes = [0, 0]  # part_log answers for the check before and after the copy
        self.prints = {"market.bars": (15, 42), "market.bars_v16": (15, 42)}
        self.commands: list[str] = []

    def query(self, sql, parameters=None, settings=None):
        p = parameters or {}
        if "FROM system.tables" in sql:
            key = self.keys.get(f"{p['db']}.{p['t']}")
            return SimpleNamespace(result_rows=[(key,)] if key else [])
        if "FROM system.columns" in sql:
            return SimpleNamespace(result_rows=self.cols[f"{p['db']}.{p['t']}"])
        if "FROM system.parts" in sql:
            return SimpleNamespace(result_rows=self.partitions)
        if "FROM system.part_log" in sql:
            return SimpleNamespace(result_rows=[(self.writes.pop(0) if self.writes else 0,)])
        if "FROM system.processes" in sql:
            return SimpleNamespace(result_rows=[(0,)])
        if "cityHash64" in sql:
            table = sql.split(" FROM ")[1].split(" FINAL")[0]
            return SimpleNamespace(result_rows=[self.prints[table]])
        raise AssertionError(sql)

    def command(self, sql, parameters=None, settings=None):
        self.commands.append(sql)
        if sql.startswith("EXCHANGE TABLES"):
            self.keys["market.bars"], self.keys["market.bars_v16"] = YEARLY, MONTHLY


def test_copies_each_partition_verifies_and_swaps_once():
    server = Server()
    repartition.apply_swap(server, BARS, out=lambda _: None)
    assert server.commands[0] == "TRUNCATE TABLE market.bars_v16"
    assert sum(c.startswith("INSERT INTO market.bars_v16") for c in server.commands) == 2
    assert server.commands[-1] == "EXCHANGE TABLES market.bars AND market.bars_v16"
    repartition.apply_swap(server, BARS, out=lambda _: None)
    assert sum(c.startswith("EXCHANGE") for c in server.commands) == 1


def test_refuses_while_writers_are_running():
    server = Server()
    server.writes = [3]
    with pytest.raises(MigrationError, match="stop its writers"):
        repartition.apply_swap(server, BARS, out=lambda _: None)
    assert not server.commands


def test_write_during_copy_or_hash_mismatch_never_swaps():
    server = Server()
    server.writes = [0, 1]
    with pytest.raises(MigrationError, match="written during the copy"):
        repartition.apply_swap(server, BARS, out=lambda _: None)
    server = Server()
    server.prints["market.bars_v16"] = (15, 41)
    with pytest.raises(MigrationError, match="does not match"):
        repartition.apply_swap(server, BARS, out=lambda _: None)
    assert not any(c.startswith("EXCHANGE") for c in server.commands)


def test_refuses_without_wave_16_or_with_drifted_columns():
    server = Server()
    del server.keys["market.bars_v16"]
    with pytest.raises(MigrationError, match="apply wave 16"):
        repartition.apply_swap(server, BARS, out=lambda _: None)
    server = Server()
    server.cols["market.bars_v16"] = server.cols["market.bars_v16"][:1]
    with pytest.raises(MigrationError, match="different columns"):
        repartition.apply_swap(server, BARS, out=lambda _: None)


def test_wave_16_differs_from_the_live_tables_only_in_the_partition_key():
    sql = (migrate.MIGRATION_DIR / "wave_16_schema_daily_partitions.sql").read_text("utf-8")
    bars = (migrate.MIGRATION_DIR / "wave_02_schema.sql").read_text("utf-8")
    cutover = (migrate.MIGRATION_DIR / "wave_09_schema_application_cutover.sql").read_text("utf-8")

    def table(text: str, name: str) -> str:
        start = text.index(f"CREATE TABLE IF NOT EXISTS {name} (")
        body = text[start : text.index(";", start)]
        return body.split("(", 1)[1].split("PARTITION BY")[0]

    assert table(sql, "market.bars_v16") == table(bars, "market.bars")
    assert table(sql, "meta.session_coverage_v16") == table(
        cutover, "meta.session_coverage_canonical"
    )
    for swap in SWAPS:
        assert swap.yearly_marker in sql
    statements = " ".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert not any(word in statements for word in ("DROP", "EXCHANGE", "INSERT"))


def test_apply_requires_confirmation(monkeypatch):
    monkeypatch.setattr(repartition, "create_client", lambda: pytest.fail("connected"))
    assert repartition.main(["apply"]) == 2


def test_identity_columns_are_each_tables_sort_key_plus_version():
    assert all(isinstance(swap, Swap) and swap.identity.endswith(", version") for swap in SWAPS)
