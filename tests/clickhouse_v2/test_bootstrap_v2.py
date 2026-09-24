"""Bootstrap refuses an incomplete v2 application schema."""

import pytest
from scripts.bootstrap_clickhouse import REQUIRED_TABLES, check_v2_readiness


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self, tables, migration_status):
        self.tables = tables
        self.migration_status = migration_status

    def query(self, sql):
        if "system.tables" in sql:
            if "sorting_key" in sql:
                return Result([(table, "country_code, listing_id") for table in (
                    "meta.expected_series", "meta.session_coverage", "meta.recovery_state"
                )])
            return Result([(table,) for table in self.tables])
        return Result([("wave_09_schema_application_cutover", self.migration_status)])


def test_ready_schema_passes():
    check_v2_readiness(Client(REQUIRED_TABLES, "succeeded"))


def test_missing_table_or_migration_fails():
    with pytest.raises(RuntimeError, match="meta.session_coverage"):
        check_v2_readiness(Client(REQUIRED_TABLES - {"meta.session_coverage"}, "succeeded"))
    with pytest.raises(RuntimeError, match="Wave 9"):
        check_v2_readiness(Client(REQUIRED_TABLES, "failed"))
