"""US futures reads stay valid ClickHouse (the fake does not execute aggregate SQL)."""

import re
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from factorlab.storage.v2_us import V2USStorage

COLUMNS = {"observed_at", "settlement_price"}


def sql_of(call: str) -> str:
    client = Mock()
    client.query.return_value = SimpleNamespace(column_names=["contract_id"], result_rows=[])
    storage = V2USStorage(client)
    if call == "closes":
        storage.futures_session_closes(date(2026, 10, 21), source="schwab")
    else:
        storage.futures_settlements(
            date(2026, 10, 21), after=datetime(2026, 10, 21, 23, tzinfo=UTC), source="schwab"
        )
    return client.query.call_args.args[0]


def test_aggregates_never_alias_a_column_other_aggregates_read() -> None:
    # Production 2026-10-21: "max(observed_at) AS observed_at" shadowed the column inside
    # the argMax calls and ClickHouse refused the query (ILLEGAL_AGGREGATION).
    for call in ("closes", "settlements"):
        sql = sql_of(call)
        aliases = set(re.findall(r"\)\s+AS\s+(\w+)", sql))
        assert not aliases & COLUMNS, (call, aliases & COLUMNS)
