"""US session coverage rewrites only days whose counts changed, plus the recent window."""

from datetime import UTC, datetime
from uuid import uuid4

from factorlab.storage.v2_us import V2USStorage
from factorlab.testkit.fake_clickhouse import FakeClickHouse, Result

START = datetime(2026, 6, 1, tzinfo=UTC)
END = datetime(2026, 9, 9, 1, tzinfo=UTC)


class Coverage(FakeClickHouse):
    """The real ``coverage`` counts bars with an aggregate the fake does not run."""

    def query(self, sql, parameters=None, column_formats=None):
        if sql.startswith("SELECT trade_date AS day, count() AS actual FROM market.bars"):
            self.queries.append(sql)
            return Result([])
        return super().query(sql, parameters, column_formats)


def test_coverage_skips_unchanged_history_but_rewrites_recent_days():
    db = Coverage()
    storage = V2USStorage(db)
    listing = uuid4()
    first = storage.coverage(listing, "AAPL", "daily", START, END, START)
    written = len(db.rows("meta.session_coverage"))
    assert first and written > 30

    again = storage.coverage(listing, "AAPL", "daily", START, END, START)
    assert again == first  # the missing count still covers every day
    rewritten = db.rows("meta.session_coverage")[written:]
    assert rewritten and len(rewritten) <= 7
    assert min(row["trade_date"] for row in rewritten) >= datetime(2026, 9, 1).date()
